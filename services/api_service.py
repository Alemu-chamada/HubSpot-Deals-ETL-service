import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from config import get_config
from loki_logger import get_logger, log_api_call


class APIService:
    """Generic shared API service for outbound HTTP calls."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        access_token: Optional[str] = None,
        timeout: Optional[int] = None,
        test_delay_seconds: float = 0,
    ):
        config = get_config()
        self.base_url = (base_url or getattr(config, 'API_BASE_URL', 'https://api.example.com')).rstrip('/')
        self.timeout = timeout or getattr(config, 'API_TIMEOUT', 30) or getattr(config, 'REQUEST_TIMEOUT', 30)
        self.test_delay_seconds = test_delay_seconds
        self.logger = get_logger(__name__)
        self.session = requests.Session()
        self._access_token: Optional[str] = None

        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Generic-Data-Extraction-Service/1.0',
        })

        if access_token:
            self.set_access_token(access_token)

        self.logger.debug(
            'API service initialized',
            extra={
                'operation': 'api_service_init',
                'base_url': self.base_url,
                'timeout': self.timeout,
                'test_delay_seconds': self.test_delay_seconds,
            },
        )

    def set_access_token(self, token: str) -> None:
        """Set the access token for shared API requests."""
        cleaned_token = (token or '').strip()
        self._access_token = cleaned_token or None
        if self._access_token:
            self.session.headers['Authorization'] = f'Bearer {self._access_token}'
        else:
            self.session.headers.pop('Authorization', None)

        self.logger.debug(
            'Access token configured',
            extra={'operation': 'token_set', 'token_present': self._access_token is not None},
        )

    def _validate_token(self, access_token: Optional[str] = None) -> str:
        """Validate and return the access token value."""
        token = (access_token or self._access_token or '').strip()
        if not token:
            raise ValueError('Access token is missing')
        return token

    def _get_headers(self, access_token: Optional[str] = None) -> Dict[str, str]:
        """Create request headers with authentication."""
        token = self._validate_token(access_token)
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _build_url(self, path: str) -> str:
        """Build a URL from the configured base URL and endpoint path."""
        return f'{self.base_url}{path}' if path.startswith('/') else f'{self.base_url}/{path}'

    def _request_json(
        self,
        method: str,
        path: str,
        access_token: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Perform a JSON request to the configured API endpoint."""
        start_time = datetime.utcnow()
        token = self._validate_token(access_token)
        url = self._build_url(path)
        headers = self._get_headers(token)

        payload_params = dict(params or {})
        for key, value in payload_params.items():
            if value is None:
                payload_params.pop(key, None)

        try:
            if self.test_delay_seconds > 0:
                self.logger.info(
                    'Applying configured test delay',
                    extra={'operation': 'api_request', 'delay_seconds': self.test_delay_seconds},
                )
                time.sleep(self.test_delay_seconds)

            self.logger.info(
                'Calling API',
                extra={
                    'operation': 'api_request',
                    'method': method.upper(),
                    'path': path,
                    'params': payload_params,
                },
            )
            response = self.session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=payload_params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            log_api_call(
                self.logger,
                'generic_api_call',
                method=method.upper(),
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                path=path,
            )
            try:
                return response.json()
            except ValueError:
                return {}
        except requests.exceptions.RequestException as exc:
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            self.logger.error(
                'API request failed',
                extra={
                    'operation': 'api_request_error',
                    'duration_ms': round(duration_ms, 2),
                    'error': str(exc),
                },
                exc_info=True,
            )
            raise

    def get_data(
        self,
        access_token: Optional[str] = None,
        limit: int = 100,
        after: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Retrieve data from the configured API endpoint with optional pagination params."""
        params = {'limit': min(max(int(limit or 50), 1), 100)}
        if after:
            params['after'] = after
        for key, value in kwargs.items():
            if key in {'access_token', 'limit', 'after'} or value is None:
                continue
            params[key] = value
        # This project extracts HubSpot Deals; retain this legacy wrapper for
        # callers that still use APIService while targeting the CRM v3 route.
        return self._request_json('GET', '/crm/v3/objects/deals', access_token=access_token, params=params)

    def validate_token(self, access_token: Optional[str] = None) -> bool:
        """Validate that an access token is accepted by the API."""
        try:
            self._request_json('GET', '/crm/v3/owners', access_token=access_token, params={'limit': 1})
            return True
        except requests.exceptions.RequestException:
            return False

    def get_api_usage(self, auth_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get usage information from the API response headers if available."""
        return None

    def get_account_info(self, access_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve account information if the API exposes it."""
        try:
            return self._request_json('GET', '/crm/v3/owners', access_token=access_token, params={'limit': 1})
        except requests.exceptions.RequestException:
            return None

    def test_connection(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Test connectivity to a shared API endpoint."""
        self.logger.info('Testing API connection', extra={'operation': 'test_connection'})
        return {
            'token_valid': self.validate_token(access_token),
            'api_reachable': self.validate_token(access_token),
            'data_accessible': False,
            'account_info': None,
            'usage_info': None,
            'error': None,
        }
