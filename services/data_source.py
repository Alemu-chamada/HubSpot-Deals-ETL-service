import dlt
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterator, Optional, Callable
from datetime import datetime, timezone

from config import get_config
from loki_logger import get_logger
from .hubspot_api_service import HubSpotAPIService


def _extract_next_cursor(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract the next cursor from a HubSpot pagination payload."""
    if not isinstance(payload, dict):
        return None

    paging = payload.get("paging")
    if isinstance(paging, dict):
        next_info = paging.get("next")
        if isinstance(next_info, dict):
            return next_info.get("after")

    return payload.get("next_after") or payload.get("next_cursor") or payload.get("next_page_token")


def _coerce_property_value(property_name: str, value: Any) -> Any:
    """Convert HubSpot property values into PostgreSQL-friendly Python types."""
    if value is None:
        return None

    if isinstance(value, (bool, int, float, Decimal)):
        return value

    if isinstance(value, str):
        stripped_value = value.strip()
        if not stripped_value:
            return None

        lowered_value = stripped_value.lower()
        if property_name in {"closedate", "createdate", "hs_lastmodifieddate"}:
            try:
                if lowered_value.endswith("z"):
                    stripped_value = stripped_value[:-1] + "+00:00"
                return datetime.fromisoformat(stripped_value)
            except ValueError:
                return stripped_value

        if property_name == "amount":
            try:
                return Decimal(stripped_value)
            except InvalidOperation:
                return stripped_value

        if lowered_value in {"true", "false"}:
            return lowered_value == "true"
        if lowered_value in {"1", "0"}:
            return lowered_value == "1"

        try:
            return int(stripped_value)
        except ValueError:
            try:
                return float(stripped_value)
            except ValueError:
                return stripped_value

    return value


def _transform_deal_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Map HubSpot deal properties to the expected database column names."""
    transformed_properties: Dict[str, Any] = {}
    property_map = {
        "dealname": "dealname",
        "amount": "amount",
        "dealstage": "dealstage",
        "closedate": "closedate",
        "createdate": "createdate",
        "hs_lastmodifieddate": "hs_lastmodifieddate",
        "hubspot_owner_id": "hubspot_owner_id",
        "description": "description",
        "pipeline": "pipeline",
        "dealtype": "dealtype",
        "is_closed": "is_closed",
    }

    for key, value in properties.items():
        destination_key = property_map.get(key, key)
        transformed_properties[destination_key] = _coerce_property_value(destination_key, value)

    return transformed_properties


def _normalize_deal_record(
    record: Any,
    filters: Dict[str, Any],
    page_number: int,
    organization_id: str,
    scan_id: str,
) -> Dict[str, Any]:
    """Normalize a raw HubSpot deal into a consistent internal record structure."""
    if not isinstance(record, dict):
        raise ValueError("Encountered malformed deal record: expected a dictionary")

    record_id = record.get("id")
    if not record_id:
        raise ValueError("Encountered malformed deal record: missing id")

    properties = record.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("Encountered malformed deal record: properties must be a dictionary")

    transformed_properties = _transform_deal_properties(properties)

    normalized_record: Dict[str, Any] = {
        "id": record_id,
        "properties": transformed_properties,
    }

    for key, value in transformed_properties.items():
        normalized_record[key] = value

    tenant_id = (
        filters.get("tenant_id")
        or filters.get("tenantId")
        or organization_id
    )

    metadata = {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "scan_id": scan_id,
        "tenant_id": tenant_id,
        "_extracted_at": datetime.now(timezone.utc).isoformat(),
        "_scan_id": scan_id,
        "_organization_id": organization_id,
        "_page_number": page_number,
        "_source_service": "hubspot_deals",
    }

    normalized_record.update(metadata)

    if "properties" in filters and filters["properties"]:
        selected_record = {
            prop: normalized_record.get(prop)
            for prop in filters["properties"]
            if prop in normalized_record
        }
        selected_record["id"] = normalized_record.get("id")
        selected_record["properties"] = transformed_properties
        selected_record.update(metadata)
        return selected_record

    return normalized_record


def create_data_source(
    job_config: Dict[str, Any],
    auth_config: Dict[str, Any],
    filters: Dict[str, Any],
    checkpoint_callback: Optional[Callable] = None,
    check_cancel_callback: Optional[Callable] = None,
    check_pause_callback: Optional[Callable] = None,
    resume_from: Optional[Dict[str, Any]] = None,
):
    """Create a DLT source function for HubSpot Deals extraction with checkpoint support."""
    logger = get_logger(__name__)
    config = get_config()
    api_service = HubSpotAPIService(
        base_url=config.HUBSPOT_API_BASE_URL,
        timeout=config.HUBSPOT_API_TIMEOUT,
        test_delay_seconds=1,
    )

    access_token = auth_config.get("accessToken")
    if not access_token:
        raise ValueError("No access token found in auth configuration")

    organization_id = job_config.get("organizationId")
    if not organization_id:
        raise ValueError("No organization ID found in job configuration")

    logger.info(
        "Starting HubSpot deals data extraction",
        extra={
            "organization_id": organization_id,
            "requested_properties": filters.get("properties"),
            "include_archived": filters.get("includeArchived", filters.get("archived")),
            "scan_id": filters.get("scan_id"),
            "access_token_present": bool(access_token),
        },
    )

    @dlt.resource(name="hubspot_deals", write_disposition="replace", primary_key="id")
    def get_main_data() -> Iterator[Dict[str, Any]]:
        """Extract HubSpot deal records and yield normalized records for downstream processing."""
        api_service.set_access_token(access_token)

        if resume_from:
            after = resume_from.get("cursor")
            page_count = resume_from.get("page_number", 0)
            total_records = resume_from.get("records_processed", 0)
            logger.info(
                "Resuming HubSpot deals extraction",
                extra={
                    "operation": "data_extraction",
                    "page_number": page_count + 1,
                    "total_processed": total_records,
                },
            )
        else:
            after = None
            page_count = 0
            total_records = 0
            logger.info(
                "Starting fresh HubSpot deals extraction",
                extra={"operation": "data_extraction", "source": "hubspot_deals"},
            )

        checkpoint_interval = 10
        cancel_check_interval = 1
        pause_check_interval = 1
        job_id = filters.get("scan_id", "unknown")
        extraction_started_at = datetime.now(timezone.utc).isoformat()
        page_size = getattr(config, "PAGINATION_DEFAULT_LIMIT", getattr(config, "DEFAULT_BATCH_SIZE", 100))
        requested_properties = filters.get("properties")
        include_archived = filters.get("includeArchived", filters.get("archived"))

        while page_count < 1000:
            try:
                if page_count % cancel_check_interval == 0:
                    if check_cancel_callback and check_cancel_callback(job_id):
                        logger.info(
                            "Extraction cancelled by user",
                            extra={
                                "operation": "data_extraction",
                                "job_id": job_id,
                                "page_number": page_count + 1,
                                "total_processed": total_records,
                            },
                        )

                        if checkpoint_callback:
                            try:
                                cancel_checkpoint = {
                                    "phase": "main_data_cancelled",
                                    "records_processed": total_records,
                                    "cursor": after,
                                    "page_number": page_count,
                                    "batch_size": page_size,
                                    "checkpoint_data": {
                                        "cancellation_reason": "user_requested",
                                        "cancelled_at_page": page_count,
                                        "service": "hubspot_deals",
                                    },
                                }
                                checkpoint_callback(job_id, cancel_checkpoint)
                            except Exception as checkpoint_error:
                                logger.warning(
                                    "Failed to save cancellation checkpoint",
                                    extra={"job_id": job_id, "error": str(checkpoint_error)},
                                )
                        break

                if page_count % pause_check_interval == 0:
                    if check_pause_callback and check_pause_callback(job_id):
                        logger.info(
                            "Extraction paused by user",
                            extra={
                                "operation": "data_extraction",
                                "job_id": job_id,
                                "page_number": page_count + 1,
                                "total_processed": total_records,
                            },
                        )

                        if checkpoint_callback:
                            try:
                                pause_checkpoint = {
                                    "phase": "main_data_paused",
                                    "records_processed": total_records,
                                    "cursor": after,
                                    "page_number": page_count,
                                    "batch_size": page_size,
                                    "checkpoint_data": {
                                        "pause_reason": "user_requested",
                                        "paused_at_page": page_count,
                                        "paused_at": datetime.now(timezone.utc).isoformat(),
                                        "service": "hubspot_deals",
                                    },
                                }
                                checkpoint_callback(job_id, pause_checkpoint)
                            except Exception as checkpoint_error:
                                logger.warning(
                                    "Failed to save pause checkpoint",
                                    extra={"job_id": job_id, "error": str(checkpoint_error)},
                                )
                        break

                logger.debug(
                    "Fetching HubSpot deals page",
                    extra={
                        "operation": "data_extraction",
                        "job_id": job_id,
                        "page_number": page_count + 1,
                    },
                )

                payload = api_service.get_deals(
                    access_token=access_token,
                    limit=page_size,
                    after=after,
                    properties=requested_properties,
                    includeArchived=include_archived,
                )

                if not isinstance(payload, dict):
                    raise ValueError("HubSpot API returned an invalid response payload")

                results = payload.get("results")
                if results is None:
                    raise ValueError("HubSpot API response is missing the results payload")
                if not isinstance(results, list):
                    raise ValueError("HubSpot API response returned malformed results data")

                page_records = 0
                for record in results:
                    if check_pause_callback and check_pause_callback(job_id):
                        logger.info(
                            "Extraction paused mid-page",
                            extra={
                                "operation": "data_extraction",
                                "job_id": job_id,
                                "page_number": page_count + 1,
                                "records_in_page": page_records,
                                "total_processed": total_records + page_records,
                            },
                        )

                        if checkpoint_callback:
                            try:
                                mid_page_checkpoint = {
                                    "phase": "main_data_paused_mid_page",
                                    "records_processed": total_records + page_records,
                                    "cursor": after,
                                    "page_number": page_count,
                                    "batch_size": page_size,
                                    "checkpoint_data": {
                                        "pause_reason": "user_requested_mid_page",
                                        "paused_at_page": page_count,
                                        "records_completed_in_page": page_records,
                                        "paused_at": datetime.now(timezone.utc).isoformat(),
                                        "service": "hubspot_deals",
                                    },
                                }
                                checkpoint_callback(job_id, mid_page_checkpoint)
                            except Exception as checkpoint_error:
                                logger.warning(
                                    "Failed to save mid-page pause checkpoint",
                                    extra={"job_id": job_id, "error": str(checkpoint_error)},
                                )
                        return

                    try:
                        normalized_record = _normalize_deal_record(
                            record=record,
                            filters=filters,
                            page_number=page_count + 1,
                            organization_id=organization_id,
                            scan_id=job_id,
                        )
                    except ValueError as validation_error:
                        logger.warning(
                            "Skipping malformed HubSpot deal record",
                            extra={
                                "operation": "data_extraction",
                                "job_id": job_id,
                                "page_number": page_count + 1,
                                "error": str(validation_error),
                            },
                        )
                        continue

                    yield normalized_record
                    page_records += 1

                total_records += page_records
                page_count += 1

                logger.info(
                    "HubSpot deals page processed",
                    extra={
                        "operation": "data_extraction",
                        "job_id": job_id,
                        "page_number": page_count,
                        "records_in_page": page_records,
                        "total_records": total_records,
                    },
                )

                if checkpoint_callback and page_count % checkpoint_interval == 0:
                    try:
                        next_cursor = _extract_next_cursor(payload)
                        checkpoint_data = {
                            "phase": "main_data",
                            "records_processed": total_records,
                            "cursor": next_cursor,
                            "page_number": page_count,
                            "batch_size": page_size,
                            "checkpoint_data": {
                                "pages_processed": page_count,
                                "last_page_records": page_records,
                                "service": "hubspot_deals",
                            },
                        }
                        checkpoint_callback(job_id, checkpoint_data)
                        logger.debug(
                            "Checkpoint saved",
                            extra={
                                "operation": "data_extraction",
                                "job_id": job_id,
                                "page_number": page_count,
                                "total_records": total_records,
                            },
                        )
                    except Exception as checkpoint_error:
                        logger.warning(
                            "Failed to save checkpoint",
                            extra={
                                "operation": "data_extraction",
                                "job_id": job_id,
                                "error": str(checkpoint_error),
                            },
                        )

                next_after = _extract_next_cursor(payload)
                if not next_after:
                    extraction_summary = {
                        "source": "hubspot_deals",
                        "extraction_started_at": extraction_started_at,
                        "extraction_completed_at": datetime.now(timezone.utc).isoformat(),
                        "total_records_retrieved": total_records,
                        "pages_processed": page_count,
                        "last_cursor": None,
                    }

                    if checkpoint_callback:
                        try:
                            final_checkpoint = {
                                "phase": "main_data_completed",
                                "records_processed": total_records,
                                "cursor": None,
                                "page_number": page_count,
                                "batch_size": page_size,
                                "checkpoint_data": {
                                    "completion_status": "success",
                                    "total_pages": page_count,
                                    "final_total": total_records,
                                    "service": "hubspot_deals",
                                    "extraction_summary": extraction_summary,
                                },
                            }
                            checkpoint_callback(job_id, final_checkpoint)
                        except Exception as checkpoint_error:
                            logger.warning(
                                "Failed to save final checkpoint",
                                extra={"job_id": job_id, "error": str(checkpoint_error)},
                            )

                    logger.info(
                        "HubSpot deals extraction completed",
                        extra={
                            "operation": "data_extraction",
                            "job_id": job_id,
                            "total_records": total_records,
                            "total_pages": page_count,
                            "extraction_summary": extraction_summary,
                        },
                    )
                    break

                after = next_after
                logger.debug(
                    "Continuing HubSpot deals pagination",
                    extra={
                        "operation": "data_extraction",
                        "job_id": job_id,
                        "next_cursor": after,
                    },
                )

            except Exception as extraction_error:
                logger.error(
                    "Error fetching HubSpot deals page",
                    extra={
                        "operation": "data_extraction",
                        "job_id": job_id,
                        "page_number": page_count + 1,
                        "error": str(extraction_error),
                    },
                    exc_info=True,
                )

                if checkpoint_callback:
                    try:
                        error_checkpoint = {
                            "phase": "main_data_error",
                            "records_processed": total_records,
                            "cursor": after,
                            "page_number": page_count,
                            "batch_size": page_size,
                            "checkpoint_data": {
                                "error": str(extraction_error),
                                "error_page": page_count + 1,
                                "recovery_cursor": after,
                                "service": "hubspot_deals",
                            },
                        }
                        checkpoint_callback(job_id, error_checkpoint)
                    except Exception:
                        pass

                raise RuntimeError(
                    f"HubSpot deals extraction failed after page {page_count + 1}: {extraction_error}"
                ) from extraction_error

    return [get_main_data]
