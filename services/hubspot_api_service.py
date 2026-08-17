import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from config import get_config
from loki_logger import get_logger, log_api_call


class HubSpotAPIError(Exception):
    """Raised when the HubSpot API returns an error response."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data


class HubSpotAPIService:
    """Service for interacting with the HubSpot Deals CRM API v3."""

    DEALS_ENDPOINT = '/crm/v3/objects/deals'

    def __init__(
        self,
        base_url: Optional[str] = None,
        access_token: Optional[str] = None,
        timeout: Optional[int] = None,
        test_delay_seconds: float = 0,
    ):
        config = get_config()
        self.base_url = (base_url or getattr(config, 'HUBSPOT_API_BASE_URL', 'https://api.hubapi.com')).rstrip('/')
        self.timeout = timeout or getattr(config, 'HUBSPOT_API_TIMEOUT', 30) or getattr(config, 'REQUEST_TIMEOUT', 30)
        self.test_delay_seconds = test_delay_seconds
        self.logger = get_logger(__name__)
        self.session = requests.Session()
        self._access_token: Optional[str] = None
        self.last_response_headers: Dict[str, str] = {}

        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'HubSpot-Deals-Data-Extraction-Service/1.0',
        })

        if access_token:
            self.set_access_token(access_token)

        self.logger.debug(
            'HubSpot API service initialized',
            extra={
                'operation': 'hubspot_api_service_init',
                'base_url': self.base_url,
                'timeout': self.timeout,
                'test_delay_seconds': self.test_delay_seconds,
            },
        )

    def set_access_token(self, token: str) -> None:
        """Set the HubSpot private app access token."""
        cleaned_token = (token or '').strip()
        self._access_token = cleaned_token or None
        if self._access_token:
            self.session.headers['Authorization'] = f'Bearer {self._access_token}'
        else:
            self.session.headers.pop('Authorization', None)

        self.logger.debug(
            'HubSpot access token configured',
            extra={'operation': 'token_set', 'token_present': self._access_token is not None},
        )

    def _validate_token(self, access_token: Optional[str] = None) -> str:
        """Validate and return the configured access token value."""
        token = (access_token or self._access_token or '').strip()
        if not token:
            raise ValueError('HubSpot private app access token is missing')
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
        """Build a HubSpot API URL from the configured base URL and endpoint path."""
        return f'{self.base_url}{path}' if path.startswith('/') else f'{self.base_url}/{path}'

    def _sleep_for_rate_limit(self, response: requests.Response) -> None:
        """Sleep for the amount requested by HubSpot when rate limiting occurs."""
        retry_after = response.headers.get('Retry-After')
        sleep_seconds = 1

        if retry_after:
            try:
                sleep_seconds = max(1, int(retry_after))
            except ValueError:
                sleep_seconds = 1
        else:
            reset_value = response.headers.get('X-RateLimit-Reset')
            if reset_value:
                try:
                    reset_time = int(reset_value)
                    now = int(time.time())
                    sleep_seconds = max(1, reset_time - now)
                except ValueError:
                    sleep_seconds = 1

        self.logger.warning(
            'HubSpot rate limit exceeded; backing off',
            extra={
                'operation': 'rate_limit_backoff',
                'retry_after': sleep_seconds,
                'status_code': response.status_code,
            },
        )
        time.sleep(sleep_seconds)

    def _request_json(
        self,
        method: str,
        path: str,
        access_token: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Perform a JSON request to the HubSpot API with retry and error handling."""
        start_time = datetime.utcnow()
        token = self._validate_token(access_token)
        url = self._build_url(path)
        headers = self._get_headers(token)

        payload_params = dict(params or {})
        for key, value in payload_params.items():
            if value is None:
                payload_params.pop(key, None)

        attempt = 0
        last_error: Optional[Exception] = None

        while attempt <= max_retries:
            if self.test_delay_seconds > 0:
                self.logger.info(
                    'Applying configured test delay',
                    extra={'operation': 'hubspot_request', 'delay_seconds': self.test_delay_seconds},
                )
                time.sleep(self.test_delay_seconds)

            try:
                self.logger.info(
                    'Calling HubSpot API',
                    extra={
                        'operation': 'hubspot_request',
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
                self.last_response_headers = dict(response.headers)

                if response.status_code == 429:
                    if attempt < max_retries:
                        self._sleep_for_rate_limit(response)
                        attempt += 1
                        continue

                if response.status_code >= 400:
                    error_payload = {}
                    try:
                        error_payload = response.json()
                    except ValueError:
                        error_payload = {'message': response.text}

                    message = error_payload.get('message') or error_payload.get('error') or response.text or 'HubSpot API request failed'
                    raise HubSpotAPIError(message, status_code=response.status_code, response_data=error_payload)

                try:
                    response_payload = response.json()
                except ValueError:
                    response_payload = {}

                duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
                log_api_call(
                    self.logger,
                    'hubspot_crm_api',
                    method=method.upper(),
                    status_code=response.status_code,
                    duration_ms=round(duration_ms, 2),
                    path=path,
                )
                return response_payload

            except HubSpotAPIError:
                raise
            except requests.exceptions.RequestException as exc:
                last_error = exc
                attempt += 1
                if attempt > max_retries:
                    break
                self.logger.warning(
                    'Transient HubSpot request failure; retrying',
                    extra={
                        'operation': 'hubspot_request_retry',
                        'attempt': attempt,
                        'error': str(exc),
                    },
                )
                time.sleep(1)

        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        self.logger.error(
            'HubSpot API request failed',
            extra={
                'operation': 'hubspot_request_error',
                'duration_ms': round(duration_ms, 2),
                'error': str(last_error),
            },
            exc_info=True,
        )
        raise HubSpotAPIError(str(last_error) or 'HubSpot API request failed', status_code=None)

    def get_deals(
        self,
        access_token: Optional[str] = None,
        limit: int = 100,
        after: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Retrieve deals from the HubSpot CRM API v3 using cursor-based pagination."""
        start_time = datetime.utcnow()
        token = self._validate_token(access_token)

        params: Dict[str, Any] = {
            'limit': min(max(int(limit or 50), 1), 100),
        }
        if after:
            params['after'] = after

        for key, value in kwargs.items():
            if key in {'access_token', 'limit', 'after'} or value is None:
                continue
            if not key.startswith('_test_') and key not in {'scan_id'}:
                params[key] = value

        # The CRM v3 API uses ``archived``. Keep accepting the extraction
        # flow's includeArchived option while sending HubSpot's query name.
        if 'includeArchived' in params and 'archived' not in params:
            params['archived'] = params.pop('includeArchived')

        # HubSpot expects the requested property names as a comma-separated
        # query parameter rather than a Python collection.
        if isinstance(params.get('properties'), (list, tuple, set)):
            params['properties'] = ','.join(str(value) for value in params['properties'])

        self.logger.info(
            'Fetching HubSpot deals',
            extra={
                'operation': 'get_deals',
                'limit': params['limit'],
                'has_cursor': after is not None,
                'cursor': after,
            },
        )

        payload = self._request_json('GET', self.DEALS_ENDPOINT, access_token=token, params=params)

        results = payload.get('results', []) if isinstance(payload, dict) else []
        paging = payload.get('paging', {}) if isinstance(payload, dict) else {}
        next_after = None
        if isinstance(paging, dict):
            next_info = paging.get('next')
            if isinstance(next_info, dict):
                next_after = next_info.get('after')

        normalized_payload = {
            'results': results,
            'paging': paging,
            'has_more': bool(next_after) or bool(payload.get('hasMore', False)) or bool(payload.get('has_more', False)),
            'next_after': next_after,
            'count': len(results),
            'error': None,
        }

        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        self.logger.info(
            'HubSpot deals retrieved successfully',
            extra={
                'operation': 'get_deals',
                'duration_ms': round(duration_ms, 2),
                'result_count': normalized_payload['count'],
                'has_more': normalized_payload['has_more'],
            },
        )
        return normalized_payload

    def get_data(
        self,
        access_token: Optional[str] = None,
        limit: int = 100,
        after: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Backward-compatible wrapper around get_deals for the existing extraction flow."""
        return self.get_deals(access_token=access_token, limit=limit, after=after, **kwargs)

    def validate_token(self, access_token: Optional[str] = None) -> bool:
        """Validate that the provided HubSpot private app token is usable."""
        try:
            self.logger.debug('Validating HubSpot access token', extra={'operation': 'validate_token'})
            self._request_json('GET', '/crm/v3/owners', access_token=access_token, params={'limit': 1})
            self.logger.info('HubSpot token validation successful', extra={'operation': 'validate_token'})
            return True
        except HubSpotAPIError as exc:
            self.logger.warning(
                'HubSpot token validation failed',
                extra={'operation': 'validate_token', 'status_code': exc.status_code, 'error': str(exc)},
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.error(
                'HubSpot token validation error',
                extra={'operation': 'validate_token', 'error': str(exc)},
                exc_info=True,
            )
            return False

    def get_api_usage(self, auth_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get rate-limit usage information from the latest HubSpot response headers."""
        try:
            access_token = auth_config.get('accessToken') or auth_config.get('token')
            if not access_token:
                return None

            self._request_json('GET', '/crm/v3/owners', access_token=access_token, params={'limit': 1})
            usage_info = {
                'limit': self.last_response_headers.get('X-RateLimit-Limit'),
                'remaining': self.last_response_headers.get('X-RateLimit-Remaining'),
                'reset_timestamp': self.last_response_headers.get('X-RateLimit-Reset'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
            filtered_usage = {k: v for k, v in usage_info.items() if v is not None}

            if filtered_usage:
                self.logger.debug(
                    'HubSpot API usage info retrieved',
                    extra={
                        'operation': 'get_api_usage',
                        'remaining': filtered_usage.get('remaining'),
                        'reset_timestamp': filtered_usage.get('reset_timestamp'),
                    },
                )
            return filtered_usage or None
        except (HubSpotAPIError, ValueError, requests.exceptions.RequestException) as exc:
            self.logger.warning(
                'Could not retrieve HubSpot API usage',
                extra={'operation': 'get_api_usage', 'error': str(exc)},
            )
            return None

    def get_account_info(self, access_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch a lightweight account/owner payload from HubSpot for connectivity checks."""
        try:
            payload = self._request_json('GET', '/crm/v3/owners', access_token=access_token, params={'limit': 1})
            self.logger.debug(
                'Account info retrieved',
                extra={
                    'operation': 'get_account_info',
                    'owner_count': len(payload.get('results', [])) if isinstance(payload, dict) else 0,
                },
            )
            return payload
        except HubSpotAPIError as exc:
            self.logger.debug(
                'Account info not available',
                extra={'operation': 'get_account_info', 'error': str(exc)},
            )
            return None

    def test_connection(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Test connection to the HubSpot API."""
        self.logger.info('Testing HubSpot API connection', extra={'operation': 'test_connection'})

        results = {
            'token_valid': False,
            'api_reachable': False,
            'data_accessible': False,
            'account_info': None,
            'usage_info': None,
            'error': None,
        }

        try:
            results['token_valid'] = self.validate_token(access_token)
            results['api_reachable'] = results['token_valid']

            if results['token_valid']:
                results['account_info'] = self.get_account_info(access_token)
                results['usage_info'] = self.get_api_usage({'accessToken': access_token})

                try:
                    self.get_data(access_token=access_token, limit=1)
                    results['data_accessible'] = True
                    self.logger.info(
                        'HubSpot connection test successful',
                        extra={
                            'operation': 'test_connection',
                            'token_valid': results['token_valid'],
                            'data_accessible': results['data_accessible'],
                        },
                    )
                except Exception as exc:
                    self.logger.warning(
                        'HubSpot data access test failed',
                        extra={'operation': 'test_connection', 'error': str(exc)},
                    )
            else:
                self.logger.warning('HubSpot connection test failed - invalid token', extra={'operation': 'test_connection'})
        except Exception as exc:
            results['error'] = str(exc)
            self.logger.error(
                'HubSpot connection test error',
                extra={'operation': 'test_connection', 'error': str(exc)},
                exc_info=True,
            )

        return results
