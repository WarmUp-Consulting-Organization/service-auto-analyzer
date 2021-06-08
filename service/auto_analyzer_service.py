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
from utils import utils
from commons.launch_objects import AnalysisResult, BatchLogInfo, AnalysisCandidate
from boosting_decision_making import boosting_featurizer
from service.analyzer_service import AnalyzerService
from amqp.amqp import AmqpClient
from commons.log_merger import LogMerger
from commons.similarity_calculator import SimilarityCalculator
import json
import logging
from time import time, sleep
from datetime import datetime
from queue import Queue
from threading import Thread

logger = logging.getLogger("analyzerApp.autoAnalyzerService")
EARLY_FINISH = False


class AutoAnalyzerService(AnalyzerService):

    def __init__(self, app_config={}, search_cfg={}):
        super(AutoAnalyzerService, self).__init__(app_config=app_config, search_cfg=search_cfg)

    def get_config_for_boosting(self, analyzer_config):
        min_should_match = self.find_min_should_match_threshold(analyzer_config) / 100
        return {
            "max_query_terms": self.search_cfg["MaxQueryTerms"],
            "min_should_match": min_should_match,
            "min_word_length": self.search_cfg["MinWordLength"],
            "filter_min_should_match_any": [],
            "filter_min_should_match": self.choose_fields_to_filter_strict(
                analyzer_config.numberOfLogLines),
            "number_of_log_lines": analyzer_config.numberOfLogLines,
            "filter_by_unique_id": True,
            "boosting_model": self.search_cfg["BoostModelFolder"]
        }

    def choose_fields_to_filter_strict(self, log_lines):
        return [
            "detected_message", "stacktrace", "potential_status_codes"]\
            if log_lines == -1 else ["message", "potential_status_codes"]

    def add_constraints_for_launches_into_query(self, query, launch):
        if launch.analyzerConfig.analyzerMode in ["LAUNCH_NAME"]:
            query["query"]["bool"]["must"].append(
                {"term": {
                    "launch_name": {
                        "value": launch.launchName}}})
        elif launch.analyzerConfig.analyzerMode in ["CURRENT_LAUNCH"]:
            query["query"]["bool"]["must"].append(
                {"term": {
                    "launch_id": {
                        "value": launch.launchId}}})
        else:
            query["query"]["bool"]["should"].append(
                {"term": {
                    "launch_name": {
                        "value": launch.launchName,
                        "boost": abs(self.search_cfg["BoostLaunch"])}}})
        return query

    def get_min_should_match_setting(self, launch):
        return "{}%".format(launch.analyzerConfig.minShouldMatch)\
            if launch.analyzerConfig.minShouldMatch > 0\
            else self.search_cfg["MinShouldMatch"]

    def build_analyze_query(self, launch, log, size=10):
        """Build analyze query"""
        min_should_match = self.get_min_should_match_setting(launch)

        query = self.build_common_query(log, size=size)
        query = self.add_constraints_for_launches_into_query(query, launch)

        if log["_source"]["message"].strip():
            log_lines = launch.analyzerConfig.numberOfLogLines
            query["query"]["bool"]["filter"].append({"term": {"is_merged": False}})
            if log_lines == -1:
                query["query"]["bool"]["must"].append(
                    self.build_more_like_this_query(min_should_match,
                                                    log["_source"]["detected_message"],
                                                    field_name="detected_message",
                                                    boost=4.0))
                if log["_source"]["stacktrace"].strip():
                    query["query"]["bool"]["must"].append(
                        self.build_more_like_this_query(min_should_match,
                                                        log["_source"]["stacktrace"],
                                                        field_name="stacktrace",
                                                        boost=2.0))
                else:
                    query["query"]["bool"]["must_not"].append({"wildcard": {"stacktrace": "*"}})
            else:
                query["query"]["bool"]["must"].append(
                    self.build_more_like_this_query(min_should_match,
                                                    log["_source"]["message"],
                                                    field_name="message",
                                                    boost=4.0))
                query["query"]["bool"]["should"].append(
                    self.build_more_like_this_query("80%",
                                                    log["_source"]["detected_message"],
                                                    field_name="detected_message",
                                                    boost=2.0))
                query["query"]["bool"]["should"].append(
                    self.build_more_like_this_query("60%",
                                                    log["_source"]["stacktrace"],
                                                    field_name="stacktrace", boost=1.0))
            query["query"]["bool"]["should"].append(
                self.build_more_like_this_query("80%",
                                                log["_source"]["merged_small_logs"],
                                                field_name="merged_small_logs",
                                                boost=0.5))
            query["query"]["bool"]["should"].append(
                self.build_more_like_this_query("1",
                                                log["_source"]["only_numbers"],
                                                field_name="only_numbers",
                                                boost=4.0,
                                                override_min_should_match="1"))
        else:
            query["query"]["bool"]["filter"].append({"term": {"is_merged": True}})
            query["query"]["bool"]["must_not"].append({"wildcard": {"message": "*"}})
            query["query"]["bool"]["must"].append(
                self.build_more_like_this_query(min_should_match,
                                                log["_source"]["merged_small_logs"],
                                                field_name="merged_small_logs",
                                                boost=2.0))
        if log["_source"]["found_exceptions"].strip():
            query["query"]["bool"]["must"].append(
                self.build_more_like_this_query("1",
                                                log["_source"]["found_exceptions"],
                                                field_name="found_exceptions",
                                                boost=4.0,
                                                override_min_should_match="1"))
        if log["_source"]["potential_status_codes"].strip():
            query["query"]["bool"]["should"].append(
                self.build_more_like_this_query("1",
                                                log["_source"]["potential_status_codes"],
                                                field_name="potential_status_codes",
                                                boost=4.0,
                                                override_min_should_match="1"))

        return query

    def build_query_with_no_defect(self, launch, log, size=10):
        min_should_match = self.get_min_should_match_setting(launch)
        query = {
            "size": size,
            "sort": ["_score",
                     {"start_time": "desc"}],
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"log_level": {"gte": utils.ERROR_LOGGING_LEVEL}}},
                        {"exists": {"field": "issue_type"}},
                        {"term": {"is_merged": False}}
                    ],
                    "must_not": [
                        {"wildcard": {"issue_type": "TI*"}},
                        {"wildcard": {"issue_type": "ti*"}},
                        {"term": {"test_item": log["_source"]["test_item"]}}
                    ],
                    "must": [
                        {"term": {"unique_id": log["_source"]["unique_id"]}},
                        {"term": {"test_case_hash": log["_source"]["test_case_hash"]}}
                    ],
                    "should": []
                }}}
        query = self.add_constraints_for_launches_into_query(query, launch)
        query["query"]["bool"]["must"].append(
            self.build_more_like_this_query(min_should_match,
                                            log["_source"]["message"],
                                            field_name="message"))
        return query

    def find_relevant_with_no_defect(self, candidates_with_no_defect, boosting_config):
        for log_info, search_res in candidates_with_no_defect:
            no_defect_candidate_exists = False
            for log in search_res["hits"]["hits"]:
                if log["_source"]["issue_type"][:2].lower() == "nd":
                    no_defect_candidate_exists = True
            _similarity_calculator = SimilarityCalculator(
                boosting_config,
                weighted_similarity_calculator=self.weighted_log_similarity_calculator)
            if no_defect_candidate_exists:
                _similarity_calculator.find_similarity(
                    [(log_info, search_res)],
                    ["message", "merged_small_logs"])
                latest_type = None
                latest_item = None
                for obj in reversed(search_res["hits"]["hits"]):
                    group_id = (obj["_id"], log_info["_id"])
                    if group_id in _similarity_calculator.similarity_dict["message"]:
                        sim_val = _similarity_calculator.similarity_dict["message"][group_id]
                        if sim_val["both_empty"]:
                            sim_val = _similarity_calculator.similarity_dict["merged_small_logs"][group_id]
                        threshold = self.search_cfg["NoDefectMinSimilarity"]
                        if not sim_val["both_empty"] and sim_val["similarity"] >= threshold:
                            latest_type = obj["_source"]["issue_type"]
                            latest_item = obj
                if latest_type and latest_type[:2].lower() == "nd":
                    return latest_item
        return None

    def _send_result_to_queue(self, test_item_dict, batches, batch_logs):
        t_start = time()
        partial_res = self.es_client.es_client.msearch("\n".join(batches) + "\n")["responses"]
        avg_time_processed = (time() - t_start) / (len(partial_res) if partial_res else 1)
        for test_item_id in test_item_dict:
            candidates = []
            candidates_with_no_defect = []
            time_processed = 0.0
            for ind in test_item_dict[test_item_id]:
                batch_log_info = batch_logs[ind]
                if batch_log_info.query_type == "without no defect":
                    candidates.append(
                        (batch_log_info.log_info, partial_res[ind]))
                if batch_log_info.query_type == "with no defect":
                    candidates_with_no_defect.append(
                        (batch_log_info.log_info, partial_res[ind]))
                time_processed += avg_time_processed
            self.queue.put(AnalysisCandidate(
                analyzerConfig=batch_log_info.analyzerConfig,
                testItemId=batch_log_info.testItemId,
                project=batch_log_info.project,
                launchId=batch_log_info.launchId,
                launchName=batch_log_info.launchName,
                timeProcessed=time_processed,
                candidates=candidates,
                candidatesWithNoDefect=candidates_with_no_defect
            ))

    def _query_elasticsearch(self, launches, max_batch_size=30):
        t_start = time()
        batches = []
        batch_logs = []
        index_in_batch = 0
        test_item_dict = {}
        batch_size = 5
        n_first_blocks = 3
        test_items_number_to_process = 0
        try:
            for launch in launches:
                index_name = utils.unite_project_name(
                    str(launch.project), self.app_config["esProjectIndexPrefix"])
                if not self.es_client.index_exists(index_name):
                    continue
                if test_items_number_to_process >= 4000:
                    logger.info("Only first 4000 test items were taken")
                    break
                if EARLY_FINISH:
                    logger.info("Early finish from analyzer before timeout")
                    break
                for test_item in launch.testItems:
                    if test_items_number_to_process >= 4000:
                        logger.info("Only first 4000 test items were taken")
                        break
                    if EARLY_FINISH:
                        logger.info("Early finish from analyzer before timeout")
                        break
                    unique_logs = utils.leave_only_unique_logs(test_item.logs)
                    prepared_logs = [self.log_preparation._prepare_log(launch, test_item, log, index_name)
                                     for log in unique_logs if log.logLevel >= utils.ERROR_LOGGING_LEVEL]
                    results = LogMerger.decompose_logs_merged_and_without_duplicates(prepared_logs)

                    for log in results:
                        message = log["_source"]["message"].strip()
                        merged_logs = log["_source"]["merged_small_logs"].strip()
                        if log["_source"]["log_level"] < utils.ERROR_LOGGING_LEVEL or\
                                (not message and not merged_logs):
                            continue
                        for query_type, query in [
                                ("without no defect", self.build_analyze_query(launch, log)),
                                ("with no defect", self.build_query_with_no_defect(launch, log))]:
                            full_query = "{}\n{}".format(
                                json.dumps({"index": index_name}), json.dumps(query))
                            batches.append(full_query)
                            batch_logs.append(BatchLogInfo(
                                analyzerConfig=launch.analyzerConfig,
                                testItemId=test_item.testItemId,
                                log_info=log,
                                query_type=query_type,
                                project=launch.project,
                                launchId=launch.launchId,
                                launchName=launch.launchName
                            ))
                            if test_item.testItemId not in test_item_dict:
                                test_item_dict[test_item.testItemId] = []
                            test_item_dict[test_item.testItemId].append(index_in_batch)
                            index_in_batch += 1
                    if n_first_blocks <= 0:
                        batch_size = max_batch_size
                    if len(batches) >= batch_size:
                        n_first_blocks -= 1
                        self._send_result_to_queue(test_item_dict, batches, batch_logs)
                        batches = []
                        batch_logs = []
                        test_item_dict = {}
                        index_in_batch = 0
                    test_items_number_to_process += 1
            if len(batches) > 0:
                self._send_result_to_queue(test_item_dict, batches, batch_logs)

        except Exception as err:
            logger.error("Error in ES query")
            logger.error(err)
        self.finished_queue.put("Finished")
        logger.info("Es queries finished %.2f s.", time() - t_start)

    @utils.ignore_warnings
    def analyze_logs(self, launches, timeout=300):
        global EARLY_FINISH
        cnt_launches = len(launches)
        logger.info("Started analysis for %d launches", cnt_launches)
        logger.info("ES Url %s", utils.remove_credentials_from_url(self.es_client.host))
        self.queue = Queue()
        self.finished_queue = Queue()
        defect_type_model_to_use = {}
        es_query_thread = Thread(target=self._query_elasticsearch, args=(launches, ))
        es_query_thread.daemon = True
        es_query_thread.start()
        try:
            results = []
            t_start = time()
            del launches

            cnt_items_to_process = 0
            results_to_share = {}
            chosen_namespaces = {}
            while self.finished_queue.empty() or not self.queue.empty():
                if (timeout - (time() - t_start)) <= 5:  # check whether we are running out of time
                    EARLY_FINISH = True
                    break
                if self.queue.empty():
                    sleep(0.1)
                    continue
                else:
                    analyzer_candidates = self.queue.get()
                try:
                    project_id = analyzer_candidates.project
                    launch_id = analyzer_candidates.launchId
                    if launch_id not in results_to_share:
                        results_to_share[launch_id] = {
                            "not_found": 0, "items_to_process": 0, "processed_time": 0,
                            "launch_id": launch_id,
                            "launch_name": analyzer_candidates.launchName,
                            "project_id": project_id,
                            "method": "auto_analysis",
                            "gather_date": datetime.now().strftime("%Y-%m-%d"),
                            "gather_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "number_of_log_lines": analyzer_candidates.analyzerConfig.numberOfLogLines,
                            "min_should_match": self.find_min_should_match_threshold(
                                analyzer_candidates.analyzerConfig),
                            "model_info": set(),
                            "module_version": [self.app_config["appVersion"]],
                            "errors": [],
                            "errors_count": 0}

                    t_start_item = time()
                    cnt_items_to_process += 1
                    results_to_share[launch_id]["items_to_process"] += 1
                    results_to_share[launch_id]["processed_time"] += analyzer_candidates.timeProcessed
                    boosting_config = self.get_config_for_boosting(analyzer_candidates.analyzerConfig)

                    relevant_with_no_defect = self.find_relevant_with_no_defect(
                        analyzer_candidates.candidatesWithNoDefect, boosting_config)
                    if relevant_with_no_defect is not None:
                        analysis_result = AnalysisResult(
                            testItem=analyzer_candidates.testItemId,
                            issueType=relevant_with_no_defect["_source"]["issue_type"],
                            relevantItem=relevant_with_no_defect["_source"]["test_item"])
                        results.append(analysis_result)
                        logger.debug("Found relevant item with No defect %s", analysis_result)
                        continue

                    if project_id not in chosen_namespaces:
                        chosen_namespaces[project_id] = self.namespace_finder.get_chosen_namespaces(
                            project_id)
                    boosting_config["chosen_namespaces"] = chosen_namespaces[project_id]
                    _boosting_decision_maker = self.model_chooser.choose_model(
                        project_id, "auto_analysis_model/",
                        custom_model_prob=self.search_cfg["ProbabilityForCustomModelAutoAnalysis"])

                    boosting_data_gatherer = boosting_featurizer.BoostingFeaturizer(
                        analyzer_candidates.candidates,
                        boosting_config,
                        feature_ids=_boosting_decision_maker.get_feature_ids(),
                        weighted_log_similarity_calculator=self.weighted_log_similarity_calculator)
                    if project_id not in defect_type_model_to_use:
                        defect_type_model_to_use[project_id] = self.model_chooser.choose_model(
                            project_id, "defect_type_model/")
                    boosting_data_gatherer.set_defect_type_model(defect_type_model_to_use[project_id])
                    feature_data, issue_type_names = boosting_data_gatherer.gather_features_info()
                    model_info_tags = boosting_data_gatherer.get_used_model_info() +\
                        _boosting_decision_maker.get_model_info()
                    results_to_share[launch_id]["model_info"].update(model_info_tags)

                    if len(feature_data) > 0:

                        predicted_labels, predicted_labels_probability =\
                            _boosting_decision_maker.predict(feature_data)

                        scores_by_issue_type = boosting_data_gatherer.scores_by_issue_type

                        for i in range(len(issue_type_names)):
                            logger.debug("Most relevant item with issue type %s has id %s",
                                         issue_type_names[i],
                                         boosting_data_gatherer.
                                         scores_by_issue_type[issue_type_names[i]]["mrHit"]["_id"])
                            logger.debug("Issue type %s has label %d and probability %.3f for features %s",
                                         issue_type_names[i],
                                         predicted_labels[i],
                                         predicted_labels_probability[i][1],
                                         feature_data[i])

                        predicted_issue_type = utils.choose_issue_type(
                            predicted_labels,
                            predicted_labels_probability,
                            issue_type_names,
                            boosting_data_gatherer.scores_by_issue_type)

                        if predicted_issue_type:
                            chosen_type = scores_by_issue_type[predicted_issue_type]
                            relevant_item = chosen_type["mrHit"]["_source"]["test_item"]
                            analysis_result = AnalysisResult(testItem=analyzer_candidates.testItemId,
                                                             issueType=predicted_issue_type,
                                                             relevantItem=relevant_item)
                            results.append(analysis_result)
                            logger.debug(analysis_result)
                        else:
                            results_to_share[launch_id]["not_found"] += 1
                            logger.debug("Test item %s has no relevant items", analyzer_candidates.testItemId)
                    else:
                        results_to_share[launch_id]["not_found"] += 1
                        logger.debug("There are no results for test item %s", analyzer_candidates.testItemId)
                    results_to_share[launch_id]["processed_time"] += (
                        time() - t_start_item)
                except Exception as err:
                    logger.error(err)
                    if launch_id in results_to_share:
                        results_to_share[launch_id]["errors"].append(
                            utils.extract_exception(err))
                        results_to_share[launch_id]["errors_count"] += 1
            if "amqpUrl" in self.app_config and self.app_config["amqpUrl"].strip():
                for launch_id in results_to_share:
                    results_to_share[launch_id]["model_info"] = list(
                        results_to_share[launch_id]["model_info"])
                AmqpClient(self.app_config["amqpUrl"]).send_to_inner_queue(
                    self.app_config["exchangeName"], "stats_info", json.dumps(results_to_share))
        except Exception as err:
            logger.error(err)
        es_query_thread.join()
        EARLY_FINISH = False
        self.queue = Queue()
        self.finished_queue = Queue()
        logger.debug("Stats info %s", results_to_share)
        logger.info("Processed %d test items. It took %.2f sec.", cnt_items_to_process, time() - t_start)
        logger.info("Finished analysis for %d launches with %d results.", cnt_launches, len(results))
        return results
