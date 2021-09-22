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
from utils import utils
from commons.launch_objects import SearchLogInfo, Log
from commons.log_preparation import LogPreparation
from boosting_decision_making import weighted_similarity_calculator
from commons import similarity_calculator
import elasticsearch
import elasticsearch.helpers
import logging
from time import time

logger = logging.getLogger("analyzerApp.searchService")


class SearchService:

    def __init__(self, app_config={}, search_cfg={}):
        self.app_config = app_config
        self.search_cfg = search_cfg
        self.es_client = EsClient(app_config=app_config, search_cfg=search_cfg)
        self.log_preparation = LogPreparation()
        self.weighted_log_similarity_calculator = None
        if self.search_cfg["SimilarityWeightsFolder"].strip():
            self.weighted_log_similarity_calculator = weighted_similarity_calculator.\
                WeightedSimilarityCalculator(folder=self.search_cfg["SimilarityWeightsFolder"])

    def build_search_query(self, search_req, queried_log, search_min_should_match="95%"):
        """Build search query"""
        query = {
            "_source": ["message", "test_item", "detected_message", "stacktrace", "potential_status_codes"],
            "size": self.app_config["esChunkNumber"],
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"log_level": {"gte": utils.ERROR_LOGGING_LEVEL}}},
                        {"exists": {"field": "issue_type"}},
                        {"term": {"is_merged": False}},
                    ],
                    "must_not": {
                        "term": {"test_item": {"value": search_req.itemId, "boost": 1.0}}
                    },
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"wildcard": {"issue_type": "TI*"}},
                                    {"wildcard": {"issue_type": "ti*"}},
                                ]
                            }
                        },
                        {"terms": {"launch_id": search_req.filteredLaunchIds}},
                        utils.build_more_like_this_query(
                            search_min_should_match,
                            queried_log["_source"]["message"],
                            field_name="message", boost=1.0,
                            override_min_should_match=None,
                            max_query_terms=self.search_cfg["MaxQueryTerms"])

                    ],
                    "should": [
                        {"term": {"is_auto_analyzed": {"value": "false", "boost": 1.0}}},
                    ]}}}
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

    def search_logs(self, search_req):
        """Get all logs similar to given logs"""
        similar_log_ids = set()
        logger.info("Started searching by request %s", search_req.json())
        logger.info("ES Url %s", utils.remove_credentials_from_url(self.es_client.host))
        index_name = utils.unite_project_name(
            str(search_req.projectId), self.app_config["esProjectIndexPrefix"])
        t_start = time()
        if not self.es_client.index_exists(index_name):
            return []
        searched_logs = set()
        test_item_info = {}

        for message in search_req.logMessages:
            if not message.strip():
                continue

            queried_log = self.log_preparation._create_log_template()
            queried_log = self.log_preparation._fill_log_fields(
                queried_log,
                Log(logId=0, message=message),
                search_req.logLines)

            msg_words = " ".join(utils.split_words(queried_log["_source"]["message"]))
            if not msg_words.strip() or msg_words in searched_logs:
                continue
            searched_logs.add(msg_words)
            search_min_should_match = utils.calculate_threshold_for_text(
                queried_log["_source"]["message"],
                self.search_cfg["SearchLogsMinSimilarity"])
            query = self.build_search_query(
                search_req,
                queried_log,
                search_min_should_match=utils.prepare_es_min_should_match(
                    search_min_should_match))
            res = []
            for r in elasticsearch.helpers.scan(self.es_client.es_client,
                                                query=query,
                                                index=index_name):
                test_item_info[r["_id"]] = r["_source"]["test_item"]
                res.append(r)
                if len(res) >= 10000:
                    break
            res = {"hits": {"hits": res}}

            _similarity_calculator = similarity_calculator.SimilarityCalculator(
                {
                    "max_query_terms": self.search_cfg["MaxQueryTerms"],
                    "min_word_length": self.search_cfg["MinWordLength"],
                    "min_should_match": "95%",
                    "number_of_log_lines": search_req.logLines
                },
                weighted_similarity_calculator=self.weighted_log_similarity_calculator)
            _similarity_calculator.find_similarity(
                [(queried_log, res)], ["message", "potential_status_codes"])

            for group_id, similarity_obj in _similarity_calculator.similarity_dict["message"].items():
                log_id, _ = group_id
                similarity_percent = similarity_obj["similarity"]
                logger.debug("Log with id %s has %.3f similarity with the queried log '%s'",
                             log_id, similarity_percent, queried_log["_source"]["message"])
                potential_status_codes_match = 0.0
                _similarity_dict = _similarity_calculator.similarity_dict["potential_status_codes"]
                if group_id in _similarity_dict:
                    potential_status_codes_match = _similarity_dict[group_id]["similarity"]
                if potential_status_codes_match < 0.99:
                    continue
                if similarity_percent >= search_min_should_match:
                    similar_log_ids.add((utils.extract_real_id(log_id), int(test_item_info[log_id])))

        logger.info("Finished searching by request %s with %d results. It took %.2f sec.",
                    search_req.json(), len(similar_log_ids), time() - t_start)
        return [SearchLogInfo(logId=log_info[0],
                              testItemId=log_info[1]) for log_info in similar_log_ids]
