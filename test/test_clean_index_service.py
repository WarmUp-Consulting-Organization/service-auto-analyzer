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

import unittest
from unittest.mock import MagicMock
import json
from http import HTTPStatus
import sure # noqa
import httpretty

import commons.launch_objects as launch_objects
from service.clean_index_service import CleanIndexService
from test.test_service import TestService
from utils import utils


class TestCleanIndexService(TestService):

    @utils.ignore_warnings
    def test_clean_index(self):
        """Test cleaning index logs"""
        tests = [
            {
                "test_calls":     [{"method":         httpretty.GET,
                                    "uri":            "/2",
                                    "status":         HTTPStatus.NOT_FOUND,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/2_suggest",
                                    "status":         HTTPStatus.NOT_FOUND,
                                    }, ],
                "rq":             launch_objects.CleanIndex(ids=[1], project=2),
                "expected_count": 0
            },
            {
                "test_calls":     [{"method":         httpretty.GET,
                                    "uri":            "/rp_2",
                                    "status":         HTTPStatus.NOT_FOUND,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/rp_2_suggest",
                                    "status":         HTTPStatus.NOT_FOUND,
                                    }, ],
                "rq":             launch_objects.CleanIndex(ids=[1], project=2),
                "app_config": {
                    "esHost": "http://localhost:9200",
                    "esUser": "",
                    "esPassword": "",
                    "esVerifyCerts":     False,
                    "esUseSsl":          False,
                    "esSslShowWarn":     False,
                    "turnOffSslVerification": True,
                    "esCAcert":          "",
                    "esClientCert":      "",
                    "esClientKey":       "",
                    "appVersion":        "",
                    "minioRegion":       "",
                    "minioBucketPrefix": "",
                    "filesystemDefaultPath": "",
                    "esChunkNumber":     1000,
                    "binaryStoreType":   "minio",
                    "minioHost":         "",
                    "minioAccessKey":    "",
                    "minioSecretKey":    "",
                    "esProjectIndexPrefix": "rp_"
                },
                "expected_count": 0
            },
            {
                "test_calls":     [{"method":         httpretty.GET,
                                    "uri":            "/1",
                                    "status":         HTTPStatus.OK,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(
                                        self.search_not_merged_logs_for_delete),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rs":             utils.get_fixture(
                                        self.delete_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_merged_logs),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rs":             utils.get_fixture(self.delete_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_not_merged_logs),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.index_logs_rq),
                                    "rs":             utils.get_fixture(self.index_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1_suggest",
                                    "status":         HTTPStatus.NOT_FOUND,
                                    }],
                "rq":             launch_objects.CleanIndex(ids=[1], project=1),
                "expected_count": 1
            },
            {
                "test_calls":     [{"method":         httpretty.GET,
                                    "uri":            "/1",
                                    "status":         HTTPStatus.OK,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(
                                        self.search_not_merged_logs_for_delete),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rs":             utils.get_fixture(
                                        self.delete_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_merged_logs),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rs":             utils.get_fixture(self.delete_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_not_merged_logs),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.index_logs_rq),
                                    "rs":             utils.get_fixture(self.index_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1_suggest",
                                    "status":         HTTPStatus.OK,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/1_suggest/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_suggest_info_ids_query),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_suggest_info_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.delete_suggest_logs_rq),
                                    "rs":             utils.get_fixture(self.delete_logs_rs),
                                    }],
                "rq":             launch_objects.CleanIndex(ids=[1], project=1),
                "expected_count": 1
            },
            {
                "test_calls":     [{"method":         httpretty.GET,
                                    "uri":            "/rp_1",
                                    "status":         HTTPStatus.OK,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/rp_1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(
                                        self.search_not_merged_logs_for_delete),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rs":             utils.get_fixture(
                                        self.delete_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/rp_1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_merged_logs),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rs":             utils.get_fixture(self.delete_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/rp_1/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_not_merged_logs),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.index_logs_rq),
                                    "rs":             utils.get_fixture(self.index_logs_rs),
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/rp_1_suggest",
                                    "status":         HTTPStatus.OK,
                                    },
                                   {"method":         httpretty.GET,
                                    "uri":            "/rp_1_suggest/_search?scroll=5m&size=1000",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(self.search_suggest_info_ids_query),
                                    "rs":             utils.get_fixture(
                                        self.one_hit_search_suggest_info_rs),
                                    },
                                   {"method":         httpretty.POST,
                                    "uri":            "/_bulk?refresh=true",
                                    "status":         HTTPStatus.OK,
                                    "content_type":   "application/json",
                                    "rq":             utils.get_fixture(
                                        self.delete_suggest_logs_rq_with_prefix),
                                    "rs":             utils.get_fixture(self.delete_logs_rs),
                                    }],
                "rq":             launch_objects.CleanIndex(ids=[1], project=1),
                "app_config": {
                    "esHost": "http://localhost:9200",
                    "esUser": "",
                    "esPassword": "",
                    "esVerifyCerts":     False,
                    "esUseSsl":          False,
                    "esSslShowWarn":     False,
                    "turnOffSslVerification": True,
                    "esCAcert":          "",
                    "esClientCert":      "",
                    "esClientKey":       "",
                    "appVersion":        "",
                    "minioRegion":       "",
                    "minioBucketPrefix": "",
                    "filesystemDefaultPath": "",
                    "esChunkNumber":     1000,
                    "binaryStoreType":   "minio",
                    "minioHost":         "",
                    "minioAccessKey":    "",
                    "minioSecretKey":    "",
                    "esProjectIndexPrefix": "rp_"
                },
                "expected_count": 1
            }
        ]

        for idx, test in enumerate(tests):
            with sure.ensure('Error in the test case number: {0}', idx):
                self._start_server(test["test_calls"])
                app_config = self.app_config
                if "app_config" in test:
                    app_config = test["app_config"]
                _clean_index_service = CleanIndexService(
                    app_config=app_config,
                    search_cfg=self.get_default_search_config())
                _clean_index_service.es_client.es_client.scroll = MagicMock(
                    return_value=json.loads(utils.get_fixture(self.no_hits_search_rs)))
                _clean_index_service.suggest_info_service.es_client.es_client.scroll = MagicMock(
                    return_value=json.loads(utils.get_fixture(self.no_hits_search_rs)))

                response = _clean_index_service.delete_logs(test["rq"])

                test["expected_count"].should.equal(response)

                TestCleanIndexService.shutdown_server(test["test_calls"])


if __name__ == '__main__':
    unittest.main()
