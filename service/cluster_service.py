"""
* Copyright 2019 EPAM Systems
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
"""
from commons.esclient import EsClient
from commons import clusterizer
from utils import utils
from commons.launch_objects import ClusterResult, ClusterInfo
from commons.log_preparation import LogPreparation
from sklearn.feature_extraction.text import CountVectorizer
import numpy as np
from amqp.amqp import AmqpClient
import json
import logging
from time import time
from datetime import datetime
import hashlib

logger = logging.getLogger("analyzerApp.clusterService")


class ClusterService:

    def __init__(self, app_config={}, search_cfg={}):
        self.app_config = app_config
        self.search_cfg = search_cfg
        self.es_client = EsClient(app_config=app_config, search_cfg=search_cfg)
        self.log_preparation = LogPreparation()

    def build_search_similar_items_query(self, queried_log, message,
                                         same_launch=False,
                                         min_should_match="95%"):
        """Build search query"""
        query = {
            "_source": ["whole_message", "test_item",
                        "detected_message", "stacktrace", "launch_id", "cluster_id",
                        "cluster_message"],
            "size": 10,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"log_level": {"gte": utils.ERROR_LOGGING_LEVEL}}},
                        {"exists": {"field": "issue_type"}},
                        {"term": {"is_merged": False}},
                    ],
                    "must_not": {
                        "term": {"test_item": {"value": queried_log["_source"]["test_item"],
                                               "boost": 1.0}}
                    },
                    "must": [
                        utils.build_more_like_this_query(
                            min_should_match, message,
                            field_name="whole_message", boost=1.0,
                            override_min_should_match=None,
                            max_query_terms=self.search_cfg["MaxQueryTerms"])
                    ]}}}
        if same_launch:
            query["query"]["bool"]["must"].append(
                {"term": {"launch_id": queried_log["_source"]["launch_id"]}})
            query["query"]["bool"]["should"] = [{"wildcard": {"cluster_message": "*"}}]
        else:
            query["query"]["bool"]["must"].append({"wildcard": {"cluster_message": "*"}})
        if queried_log["_source"]["found_exceptions"].strip():
            query["query"]["bool"]["must"].append(
                utils.build_more_like_this_query(
                    "1",
                    queried_log["_source"]["found_exceptions"],
                    field_name="found_exceptions", boost=1.0,
                    override_min_should_match="1",
                    max_query_terms=self.search_cfg["MaxQueryTerms"]))
        if queried_log["_source"]["potential_status_codes"].strip():
            number_of_status_codes = str(len(set(
                queried_log["_source"]["potential_status_codes"].split())))
            query["query"]["bool"]["must"].append(
                utils.build_more_like_this_query(
                    "1",
                    queried_log["_source"]["potential_status_codes"],
                    field_name="potential_status_codes", boost=1.0,
                    override_min_should_match=number_of_status_codes,
                    max_query_terms=self.search_cfg["MaxQueryTerms"]))
        return query

    def find_similar_items_from_es(
            self, groups, log_dict,
            log_messages, log_ids, number_of_lines,
            additional_results,
            same_launch=False):
        new_clusters = {}
        _clusterizer = clusterizer.Clusterizer()
        for global_group in groups:
            first_item_ind = groups[global_group][0]
            min_should_match = utils.calculate_threshold_for_text(
                log_messages[first_item_ind],
                self.search_cfg["ClusterLogsMinSimilarity"])
            query = self.build_search_similar_items_query(
                log_dict[first_item_ind],
                log_messages[first_item_ind],
                same_launch=same_launch,
                min_should_match=utils.prepare_es_min_should_match(
                    min_should_match))
            search_results = self.es_client.es_client.search(
                index=log_dict[first_item_ind]["_index"],
                body=query)
            log_messages_part = [log_messages[first_item_ind]]
            log_dict_part = {0: log_dict[first_item_ind]}
            ind = 1
            for res in search_results["hits"]["hits"]:
                if int(res["_id"]) in log_ids:
                    continue
                log_dict_part[ind] = res
                log_message = utils.prepare_message_for_clustering(
                    res["_source"]["whole_message"], number_of_lines)
                if not log_message.strip():
                    continue
                log_messages_part.append(log_message)
                ind += 1
            groups_part = _clusterizer.find_clusters(log_messages_part, threshold=min_should_match)
            new_group = None
            for group in groups_part:
                if 0 in groups_part[group] and len(groups_part[group]) > 1:
                    cluster_id = 0
                    cluster_message = ""
                    for ind in groups_part[group]:
                        if log_dict_part[ind]["_source"]["cluster_id"].strip() and int(
                                log_dict_part[ind]["_source"]["cluster_id"].strip()) != 0:
                            cluster_id = int(log_dict_part[ind]["_source"]["cluster_id"].strip())
                            if log_dict_part[ind]["_source"]["cluster_message"].strip():
                                cluster_message = log_dict_part[ind]["_source"]["cluster_message"]
                    new_group_log_ids = []
                    for ind in groups_part[group]:
                        if ind == 0:
                            continue
                        log_ids.add(int(log_dict_part[ind]["_id"]))
                        new_group_log_ids.append(log_dict_part[ind]["_id"])
                    new_group = ClusterInfo(
                        logIds=new_group_log_ids,
                        clusterMessage=cluster_message,
                        clusterId=cluster_id)
                    break
            if new_group:
                new_clusters[global_group] = new_group
        for group in new_clusters:
            if group in additional_results:
                additional_results[group].logIds.extend(new_clusters[group].logIds)
            else:
                additional_results[group] = new_clusters[group]
        return additional_results

    def calculate_hash(self, group_ids, log_dict, log_messages, number_of_lines):
        group_logs = []
        log_message = ""
        for i in range(min(100, len(group_ids))):
            ind = group_ids[i]
            group_logs.append(log_messages[ind])
            if not log_message:
                log_message = utils.first_lines(
                    log_dict[ind]["_source"]["whole_message"], number_of_lines).strip()
        _cnt_vectorizer = CountVectorizer(
            binary=True, analyzer="word", token_pattern="[^ ]+", ngram_range=(2, 2))
        group_res = _cnt_vectorizer.fit_transform(group_logs).astype(np.int8)
        res_bitwise = np.bitwise_and.reduce(group_res.toarray(), axis=0)
        bigrams_list = []
        for i, feature_name in enumerate(_cnt_vectorizer.get_feature_names()):
            if res_bitwise[i] == 1:
                bigrams_list.append(feature_name)
        hash_message = int(
            hashlib.sha1(" ".join(bigrams_list).encode("utf-8")).hexdigest(), 16) % (10 ** 16)
        return hash_message, log_message

    def gather_cluster_results(
            self, groups, additional_results, log_dict, log_messages, number_of_lines):
        results_to_return = []
        cluster_num = 0
        for group in groups:
            cnt_items = len(groups[group])
            cluster_id = 0
            cluster_message = ""
            if group in additional_results:
                cnt_items += len(additional_results[group].logIds)
                cluster_id = additional_results[group].clusterId
                cluster_message = additional_results[group].clusterMessage
            if cnt_items > 1:
                cluster_num += 1
            if not cluster_id:
                cluster_id, cluster_message = self.calculate_hash(
                    groups[group], log_dict, log_messages, number_of_lines)
            log_ids = []
            for ind in groups[group]:
                log_ids.append(log_dict[ind]["_id"])
            if group in additional_results:
                log_ids.extend(additional_results[group].logIds)
            results_to_return.append(ClusterInfo(
                clusterId=cluster_id,
                clusterMessage=cluster_message,
                logIds=log_ids))
        return results_to_return, cluster_num

    def regroup_by_error_ans_status_codes(self, log_messages, log_dict):
        regroupped_by_error = {}
        for i in range(len(log_messages)):
            found_exceptions = " ".join(
                sorted(log_dict[i]["_source"]["found_exceptions"].split()))
            potential_status_codes = " ".join(
                sorted(log_dict[i]["_source"]["potential_status_codes"].split()))
            group_key = (found_exceptions, potential_status_codes)
            if group_key not in regroupped_by_error:
                regroupped_by_error[group_key] = []
            regroupped_by_error[group_key].append(i)
        return regroupped_by_error

    def cluster_messages_with_groupping_by_error(self, log_messages, log_dict):
        regroupped_by_error = self.regroup_by_error_ans_status_codes(
            log_messages, log_dict)
        _clusterizer = clusterizer.Clusterizer()
        all_groups = {}
        start_group_id = 0
        for group_key in regroupped_by_error:
            log_messages_part = []
            log_messages_idx_dict = {}
            for i, idx in enumerate(regroupped_by_error[group_key]):
                log_messages_part.append(log_messages[idx])
                log_messages_idx_dict[i] = idx
            groups = _clusterizer.find_clusters(
                log_messages_part,
                threshold=self.search_cfg["ClusterLogsMinSimilarity"])
            max_group_id = max(groups.keys())
            for group_id in groups:
                global_idx = start_group_id + group_id
                if global_idx not in all_groups:
                    all_groups[global_idx] = []
                for i in groups[group_id]:
                    all_groups[global_idx].append(log_messages_idx_dict[i])
            start_group_id = start_group_id + max_group_id + 1
        return all_groups

    @utils.ignore_warnings
    def find_clusters(self, launch_info):
        logger.info("Started clusterizing logs")
        index_name = utils.unite_project_name(
            str(launch_info.launch.project), self.app_config["esProjectIndexPrefix"])
        if not self.es_client.index_exists(index_name):
            logger.info("Project %d doesn't exist", index_name)
            logger.info("Finished clustering log with 0 clusters.")
            return []
        t_start = time()
        errors_found = []
        errors_count = 0
        cluster_num = 0
        clusters = []
        log_ids = []
        try:
            log_messages, log_dict = self.log_preparation.prepare_logs_for_clustering(
                launch_info.launch, launch_info.numberOfLogLines, launch_info.cleanNumbers, index_name)
            log_ids = set([int(log["_id"]) for log in log_dict.values()])

            groups = self.cluster_messages_with_groupping_by_error(log_messages, log_dict)
            additional_results = self.find_similar_items_from_es(
                groups, log_dict, log_messages,
                log_ids, launch_info.numberOfLogLines,
                {}, same_launch=False)

            if launch_info.forUpdate:
                additional_results = self.find_similar_items_from_es(
                    groups, log_dict, log_messages,
                    log_ids, launch_info.numberOfLogLines,
                    additional_results, same_launch=True)

            clusters, cluster_num = self.gather_cluster_results(
                groups, additional_results, log_dict, log_messages, launch_info.numberOfLogLines)
            if clusters:
                bodies = []
                for result in clusters:
                    for log_id in result.logIds:
                        bodies.append({
                            "_op_type": "update",
                            "_id": log_id,
                            "_index": index_name,
                            "doc": {"cluster_id": str(result.clusterId),
                                    "cluster_message": result.clusterMessage}})
                self.es_client._bulk_index(bodies)
        except Exception as err:
            logger.error(err)
            errors_found.append(utils.extract_exception(err))
            errors_count += 1

        results_to_share = {launch_info.launch.launchId: {
            "not_found": int(cluster_num == 0), "items_to_process": len(log_ids),
            "processed_time": time() - t_start, "found_clusters": cluster_num,
            "launch_id": launch_info.launch.launchId, "launch_name": launch_info.launch.launchName,
            "project_id": launch_info.launch.project, "method": "find_clusters",
            "gather_date": datetime.now().strftime("%Y-%m-%d"),
            "gather_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "module_version": [self.app_config["appVersion"]],
            "model_info": [],
            "errors": errors_found,
            "errors_count": errors_count}}
        if "amqpUrl" in self.app_config and self.app_config["amqpUrl"].strip():
            AmqpClient(self.app_config["amqpUrl"]).send_to_inner_queue(
                self.app_config["exchangeName"], "stats_info", json.dumps(results_to_share))

        logger.debug("Stats info %s", results_to_share)
        logger.info("Processed the launch. It took %.2f sec.", time() - t_start)
        logger.info("Finished clustering for the launch with %d clusters.", cluster_num)
        return ClusterResult(
            project=launch_info.launch.project,
            launchId=launch_info.launch.launchId,
            clusters=clusters)
