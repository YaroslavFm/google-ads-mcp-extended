# Copyright 2026 BetterMe. Extension of the official Google Ads MCP server.
# Licensed under the Apache License, Version 2.0.

"""Account hygiene tools: Google recommendations, change history,
seasonality adjustments, data exclusions, labels.

Safety model: ``confirm=False`` (default) = dry-run preview.
Note: apply/dismiss recommendation have no validate_only in the API, so
with confirm=false they only show what WOULD be applied, without calling
the mutate endpoint.
"""

import datetime
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from google.ads.googleads.errors import GoogleAdsException

import ads_mcp.utils as utils
from ads_mcp.tools.mutate import (
    _clean_customer_id,
    _preview_or_done,
    _raise_tool_error,
)

optimize_mcp = FastMCP("optimize")

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_READ = ToolAnnotations(readOnlyHint=True)


@optimize_mcp.tool(annotations=_READ)
def recommendations_list(
    customer_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Lists Google's optimization recommendations for the account.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        limit: Max rows (default 50).
    """
    customer_id = _clean_customer_id(customer_id)
    ga_service = utils.get_googleads_service("GoogleAdsService")
    query = (
        "SELECT recommendation.resource_name, recommendation.type, "
        "recommendation.dismissed, recommendation.campaign "
        f"FROM recommendation LIMIT {int(limit)}"
    )
    try:
        rows = ga_service.search(customer_id=customer_id, query=query)
        return [
            {
                "resource_name": row.recommendation.resource_name,
                "type": row.recommendation.type_.name,
                "campaign": row.recommendation.campaign,
                "dismissed": row.recommendation.dismissed,
            }
            for row in rows
        ]
    except GoogleAdsException as ex:
        _raise_tool_error(ex)


@optimize_mcp.tool(annotations=_WRITE)
def recommendation_apply(
    customer_id: str,
    resource_names: List[str],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Applies Google recommendations (with their default parameters).

    Get resource_names from recommendations_list. NOTE: applying budget or
    bidding recommendations CAN increase spend — review each one first.
    With confirm=false nothing is sent to Google.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        resource_names: Recommendation resource names to apply.
        confirm: False = preview only (default), True = apply.
    """
    customer_id = _clean_customer_id(customer_id)
    if not resource_names:
        raise ToolError("resource_names list is empty")

    if not confirm:
        return _preview_or_done(
            False,
            "optimize_recommendation_apply",
            {
                "customer_id": customer_id,
                "would_apply": resource_names,
                "count": len(resource_names),
            },
        )

    client = utils.get_googleads_client()
    rec_service = utils.get_googleads_service("RecommendationService")

    request = client.get_type("ApplyRecommendationRequest")
    request.customer_id = customer_id
    for rn in resource_names:
        op = client.get_type("ApplyRecommendationOperation")
        op.resource_name = rn
        request.operations.append(op)

    try:
        response = rec_service.apply_recommendation(request=request)
    except GoogleAdsException as ex:
        _raise_tool_error(ex)

    return _preview_or_done(
        True,
        "optimize_recommendation_apply",
        {
            "customer_id": customer_id,
            "applied": [r.resource_name for r in response.results],
        },
    )


@optimize_mcp.tool(annotations=_WRITE)
def recommendation_dismiss(
    customer_id: str,
    resource_names: List[str],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Dismisses Google recommendations (removes them from the list).

    With confirm=false nothing is sent to Google.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        resource_names: Recommendation resource names to dismiss.
        confirm: False = preview only (default), True = dismiss.
    """
    customer_id = _clean_customer_id(customer_id)
    if not resource_names:
        raise ToolError("resource_names list is empty")

    if not confirm:
        return _preview_or_done(
            False,
            "optimize_recommendation_dismiss",
            {
                "customer_id": customer_id,
                "would_dismiss": resource_names,
                "count": len(resource_names),
            },
        )

    client = utils.get_googleads_client()
    rec_service = utils.get_googleads_service("RecommendationService")

    request = client.get_type("DismissRecommendationRequest")
    request.customer_id = customer_id
    for rn in resource_names:
        op = type(request).DismissRecommendationOperation()
        op.resource_name = rn
        request.operations.append(op)

    try:
        response = rec_service.dismiss_recommendation(request=request)
    except GoogleAdsException as ex:
        _raise_tool_error(ex)

    return _preview_or_done(
        True,
        "optimize_recommendation_dismiss",
        {
            "customer_id": customer_id,
            "dismissed": [r.resource_name for r in response.results],
        },
    )


@optimize_mcp.tool(annotations=_READ)
def change_history(
    customer_id: str,
    days: int = 7,
    campaign_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Shows recent changes in the account: who changed what and when.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        days: Look-back window in days, max 30 (API limit).
        campaign_id: Optional filter by campaign.
        limit: Max rows (default 100, API max 10000).
    """
    customer_id = _clean_customer_id(customer_id)
    days = min(int(days), 30)
    limit = min(int(limit), 10000)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    where = (
        f"WHERE change_event.change_date_time >= '{start} 00:00:00' "
        f"AND change_event.change_date_time <= '{end} 23:59:59' "
    )
    if campaign_id:
        where += f"AND campaign.id = {int(campaign_id)} "

    ga_service = utils.get_googleads_service("GoogleAdsService")
    query = (
        "SELECT change_event.change_date_time, change_event.user_email, "
        "change_event.client_type, change_event.change_resource_type, "
        "change_event.resource_change_operation, "
        "change_event.changed_fields "
        f"FROM change_event {where} "
        f"ORDER BY change_event.change_date_time DESC LIMIT {limit}"
    )
    try:
        rows = ga_service.search(customer_id=customer_id, query=query)
        return [
            {
                "when": row.change_event.change_date_time,
                "who": row.change_event.user_email,
                "via": row.change_event.client_type.name,
                "resource": row.change_event.change_resource_type.name,
                "operation": row.change_event.resource_change_operation.name,
                "fields": list(row.change_event.changed_fields.paths),
            }
            for row in rows
        ]
    except GoogleAdsException as ex:
        _raise_tool_error(ex)


def _seasonality_like_create(
    customer_id: str,
    name: str,
    start_date_time: str,
    end_date_time: str,
    campaign_ids: Optional[List[str]],
    confirm: bool,
    conversion_rate_modifier: Optional[float] = None,
):
    """Shared builder for seasonality adjustments and data exclusions."""
    client = utils.get_googleads_client()
    is_exclusion = conversion_rate_modifier is None
    if is_exclusion:
        service = utils.get_googleads_service("BiddingDataExclusionService")
        operation = client.get_type("BiddingDataExclusionOperation")
        obj = operation.create
        scope_enum = client.enums.SeasonalityEventScopeEnum
        request = client.get_type("MutateBiddingDataExclusionsRequest")
    else:
        service = utils.get_googleads_service(
            "BiddingSeasonalityAdjustmentService"
        )
        operation = client.get_type("BiddingSeasonalityAdjustmentOperation")
        obj = operation.create
        scope_enum = client.enums.SeasonalityEventScopeEnum
        obj.conversion_rate_modifier = float(conversion_rate_modifier)
        request = client.get_type("MutateBiddingSeasonalityAdjustmentsRequest")

    obj.name = name
    obj.start_date_time = start_date_time
    obj.end_date_time = end_date_time
    if campaign_ids:
        obj.scope = scope_enum.CAMPAIGN
        for cid in campaign_ids:
            obj.campaigns.append(f"customers/{customer_id}/campaigns/{cid}")
    else:
        obj.scope = scope_enum.CUSTOMER

    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = not confirm

    if is_exclusion:
        return service.mutate_bidding_data_exclusions(request=request)
    return service.mutate_bidding_seasonality_adjustments(request=request)


@optimize_mcp.tool(annotations=_WRITE)
def seasonality_adjustment_create(
    customer_id: str,
    name: str,
    start_date_time: str,
    end_date_time: str,
    expected_conversion_rate_change_percent: float,
    campaign_ids: List[str] = [],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates a seasonality adjustment: tells Smart Bidding to expect a
    temporary conversion-rate change (e.g. a sale) for a short period
    (1-7 days recommended, max 14).

    SAFETY: dry-run by default; re-run with confirm=true.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        name: Adjustment name.
        start_date_time: "YYYY-MM-DD HH:MM:SS" in account timezone.
        end_date_time: "YYYY-MM-DD HH:MM:SS".
        expected_conversion_rate_change_percent: e.g. 30 = +30% CR expected,
            -20 = -20%. Converted to modifier automatically (30 -> 1.3).
        campaign_ids: Optional list to scope to specific campaigns;
            omit for the whole account.
        confirm: False = dry-run preview (default), True = apply.
    """
    customer_id = _clean_customer_id(customer_id)
    modifier = 1 + expected_conversion_rate_change_percent / 100.0
    if not (0.1 <= modifier <= 10):
        raise ToolError("Modifier out of range (percent between -90 and 900)")

    try:
        response = _seasonality_like_create(
            customer_id,
            name,
            start_date_time,
            end_date_time,
            campaign_ids,
            confirm,
            conversion_rate_modifier=modifier,
        )
    except GoogleAdsException as ex:
        _raise_tool_error(ex)

    details: Dict[str, Any] = {
        "customer_id": customer_id,
        "name": name,
        "period": f"{start_date_time} — {end_date_time}",
        "conversion_rate_modifier": modifier,
        "scope": campaign_ids or "CUSTOMER (whole account)",
    }
    if confirm:
        details["created_resource"] = response.results[0].resource_name
    return _preview_or_done(
        confirm, "optimize_seasonality_adjustment_create", details
    )


@optimize_mcp.tool(annotations=_WRITE)
def data_exclusion_create(
    customer_id: str,
    name: str,
    start_date_time: str,
    end_date_time: str,
    campaign_ids: List[str] = [],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates a data exclusion: tells Smart Bidding to IGNORE conversion
    data from a period (tracking outage, site downtime, data anomaly).

    SAFETY: dry-run by default; re-run with confirm=true.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        name: Exclusion name.
        start_date_time: "YYYY-MM-DD HH:MM:SS" in account timezone.
        end_date_time: "YYYY-MM-DD HH:MM:SS".
        campaign_ids: Optional list to scope to specific campaigns;
            omit for the whole account.
        confirm: False = dry-run preview (default), True = apply.
    """
    customer_id = _clean_customer_id(customer_id)
    try:
        response = _seasonality_like_create(
            customer_id,
            name,
            start_date_time,
            end_date_time,
            campaign_ids,
            confirm,
        )
    except GoogleAdsException as ex:
        _raise_tool_error(ex)

    details: Dict[str, Any] = {
        "customer_id": customer_id,
        "name": name,
        "period": f"{start_date_time} — {end_date_time}",
        "scope": campaign_ids or "CUSTOMER (whole account)",
    }
    if confirm:
        details["created_resource"] = response.results[0].resource_name
    return _preview_or_done(confirm, "optimize_data_exclusion_create", details)


@optimize_mcp.tool(annotations=_WRITE)
def label_create(
    customer_id: str,
    name: str,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates a label (for marking campaigns/ad groups, useful for
    automation bookkeeping).

    SAFETY: dry-run by default; re-run with confirm=true.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        name: Label name (unique).
        confirm: False = dry-run preview (default), True = apply.
    """
    customer_id = _clean_customer_id(customer_id)
    client = utils.get_googleads_client()
    label_service = utils.get_googleads_service("LabelService")

    operation = client.get_type("LabelOperation")
    operation.create.name = name

    request = client.get_type("MutateLabelsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = not confirm

    try:
        response = label_service.mutate_labels(request=request)
    except GoogleAdsException as ex:
        _raise_tool_error(ex)

    details: Dict[str, Any] = {"customer_id": customer_id, "label": name}
    if confirm:
        details["created_resource"] = response.results[0].resource_name
    return _preview_or_done(confirm, "optimize_label_create", details)


@optimize_mcp.tool(annotations=_WRITE)
def label_apply(
    customer_id: str,
    label_id: str,
    campaign_ids: List[str] = [],
    ad_group_ids: List[str] = [],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Applies an existing label to campaigns and/or ad groups.

    Find label ids via search on resource `label`. SAFETY: dry-run by
    default; re-run with confirm=true.

    Args:
        customer_id: The client account id (digits only, no hyphens).
        label_id: The numeric id of the label.
        campaign_ids: Campaigns to label.
        ad_group_ids: Ad groups to label.
        confirm: False = dry-run preview (default), True = apply.
    """
    customer_id = _clean_customer_id(customer_id)
    if not campaign_ids and not ad_group_ids:
        raise ToolError("Pass campaign_ids and/or ad_group_ids")

    client = utils.get_googleads_client()
    label_rn = f"customers/{customer_id}/labels/{label_id}"
    results: List[str] = []

    try:
        if campaign_ids:
            service = utils.get_googleads_service("CampaignLabelService")
            request = client.get_type("MutateCampaignLabelsRequest")
            request.customer_id = customer_id
            request.validate_only = not confirm
            for cid in campaign_ids:
                op = client.get_type("CampaignLabelOperation")
                op.create.campaign = (
                    f"customers/{customer_id}/campaigns/{cid}"
                )
                op.create.label = label_rn
                request.operations.append(op)
            response = service.mutate_campaign_labels(request=request)
            if confirm:
                results += [r.resource_name for r in response.results]
        if ad_group_ids:
            service = utils.get_googleads_service("AdGroupLabelService")
            request = client.get_type("MutateAdGroupLabelsRequest")
            request.customer_id = customer_id
            request.validate_only = not confirm
            for agid in ad_group_ids:
                op = client.get_type("AdGroupLabelOperation")
                op.create.ad_group = (
                    f"customers/{customer_id}/adGroups/{agid}"
                )
                op.create.label = label_rn
                request.operations.append(op)
            response = service.mutate_ad_group_labels(request=request)
            if confirm:
                results += [r.resource_name for r in response.results]
    except GoogleAdsException as ex:
        _raise_tool_error(ex)

    details: Dict[str, Any] = {
        "customer_id": customer_id,
        "label_id": str(label_id),
        "campaign_ids": campaign_ids or [],
        "ad_group_ids": ad_group_ids or [],
    }
    if confirm:
        details["created_resources"] = results
    return _preview_or_done(confirm, "optimize_label_apply", details)
