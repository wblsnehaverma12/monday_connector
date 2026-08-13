# -*- coding: utf-8 -*-
#
#################################################################################
# Author      : Weblytic Labs Pvt. Ltd. (<https://store.weblyticlabs.com/>)
# Copyright(c): 2023-Present Weblytic Labs Pvt. Ltd.
# All Rights Reserved.
#
# This program is copyright property of the author mentioned above.
# You can`t redistribute it and/or modify it.
##################################################################################

import time
import json
import logging
import requests

_logger = logging.getLogger(__name__)


class GraphQLClient:
    """Reusable GraphQL client for Monday.com API v2"""

    def __init__(self, api_token, api_version="2024-01", base_url="https://api.monday.com/v2", log_callback=None):
        self.api_token = api_token
        self.api_version = api_version
        self.base_url = base_url
        self.log_callback = log_callback  # Function signature: (query, variables, response_text, duration, status, error_msg=None)

    def _get_headers(self, is_multipart=False):
        headers = {
            "Authorization": self.api_token,
            "API-Version": self.api_version,
        }
        if not is_multipart:
            headers["Content-Type"] = "application/json"
        return headers

    def execute(self, query, variables=None, max_retries=5, backoff_factor=2):
        """Executes a GraphQL query or mutation, retries on standard rate limits or complexity limits."""
        headers = self._get_headers()
        payload = {
            "query": query,
            "variables": variables or {}
        }
        retries = 0

        while retries < max_retries:
            start_time = time.time()
            try:
                _logger.debug("Executing GraphQL query on %s", self.base_url)
                response = requests.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    timeout=30
                )
                duration = time.time() - start_time

                # Handle HTTP Rate Limit
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", backoff_factor ** retries))
                    _logger.warning("Monday.com API Rate Limited (429). Retrying in %s seconds...", retry_after)
                    time.sleep(retry_after)
                    retries += 1
                    continue

                if response.status_code != 200:
                    err_msg = f"HTTP Error {response.status_code}: {response.text}"
                    if self.log_callback:
                        self.log_callback(query, variables, response.text, duration, "failed", err_msg)
                    raise Exception(err_msg)

                res_data = response.json()

                # Handle GraphQL specific errors
                if "errors" in res_data:
                    errors = res_data["errors"]
                    is_complexity_error = any(
                        "ComplexityBudgetExceeded" in str(err.get("message", "")) or
                        "Rate limit exceeded" in str(err.get("message", ""))
                        for err in errors
                    )

                    if is_complexity_error and retries < max_retries - 1:
                        sleep_time = (backoff_factor ** retries) * 5
                        _logger.warning("Complexity limit exceeded. Retrying in %s seconds...", sleep_time)
                        time.sleep(sleep_time)
                        retries += 1
                        continue

                    err_msg = "; ".join([err.get("message", "Unknown GraphQL error") for err in errors])
                    if self.log_callback:
                        self.log_callback(query, variables, json.dumps(res_data), duration, "failed", err_msg)
                    raise Exception(f"GraphQL Error: {err_msg}")

                if self.log_callback:
                    self.log_callback(query, variables, json.dumps(res_data), duration, "success")

                return res_data.get("data", {})

            except requests.exceptions.RequestException as e:
                duration = time.time() - start_time
                err_msg = f"Network Exception: {str(e)}"
                _logger.error(err_msg)

                if retries >= max_retries - 1:
                    if self.log_callback:
                        self.log_callback(query, variables, "", duration, "failed", err_msg)
                    raise Exception(err_msg)

                sleep_time = backoff_factor ** retries
                time.sleep(sleep_time)
                retries += 1

        raise Exception("Max retries exceeded for Monday.com GraphQL API.")

    def execute_multipart(self, query, file_name, file_content, variables=None, max_retries=5, backoff_factor=2):
        """Executes a GraphQL file upload mutation using multipart/form-data."""
        headers = self._get_headers(is_multipart=True)
        retries = 0

        # Monday.com requires the file variable inside variables, but passed separately in files dict.
        vars_payload = variables or {}
        # Ensure 'file' key exists in variables to reference in GraphQL signature
        vars_payload["file"] = None

        data_payload = {
            "query": query,
            "variables": json.dumps(vars_payload)
        }

        # Format files for requests library
        files = {
            "variables[file]": (file_name, file_content, "application/octet-stream")
        }

        while retries < max_retries:
            start_time = time.time()
            try:
                _logger.debug("Executing Multipart GraphQL query on %s", self.base_url)
                response = requests.post(
                    self.base_url,
                    data=data_payload,
                    files=files,
                    headers=headers,
                    timeout=60
                )
                duration = time.time() - start_time

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", backoff_factor ** retries))
                    _logger.warning("Monday.com API Rate Limited (429) on file upload. Retrying in %s seconds...", retry_after)
                    time.sleep(retry_after)
                    retries += 1
                    continue

                if response.status_code != 200:
                    err_msg = f"HTTP Error {response.status_code} on file upload: {response.text}"
                    if self.log_callback:
                        self.log_callback(query, vars_payload, response.text, duration, "failed", err_msg)
                    raise Exception(err_msg)

                res_data = response.json()

                if "errors" in res_data:
                    errors = res_data["errors"]
                    err_msg = "; ".join([err.get("message", "Unknown GraphQL error") for err in errors])
                    if self.log_callback:
                        self.log_callback(query, vars_payload, json.dumps(res_data), duration, "failed", err_msg)
                    raise Exception(f"GraphQL Error on file upload: {err_msg}")

                if self.log_callback:
                    self.log_callback(query, vars_payload, json.dumps(res_data), duration, "success")

                return res_data.get("data", {})

            except requests.exceptions.RequestException as e:
                duration = time.time() - start_time
                err_msg = f"Network Exception on file upload: {str(e)}"
                _logger.error(err_msg)

                if retries >= max_retries - 1:
                    if self.log_callback:
                        self.log_callback(query, vars_payload, "", duration, "failed", err_msg)
                    raise Exception(err_msg)

                sleep_time = backoff_factor ** retries
                time.sleep(sleep_time)
                retries += 1

        raise Exception("Max retries exceeded for Monday.com GraphQL API Multipart upload.")
