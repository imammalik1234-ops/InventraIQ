"""Versioned server-reviewed policy contracts. No business numeric defaults."""
from copy import deepcopy
from flask import current_app
from security import MutationError
from forecasting.artifacts import digest

VERSION='horizon-fefo-net-need-v1'
APPROVAL='synthetic-review-2026-10-23'
FIELDS={'version','approval_reference','supplier_id','lead_days','review_days','safety_units',
        'excess_buffer_units','minimum_order_units','pack_units'}


def validate(value):
    if not isinstance(value,dict) or set(value)!=FIELDS:raise MutationError('A complete versioned recommendation policy is required.')
    result=deepcopy(value)
    if result['approval_reference']!=APPROVAL:raise MutationError('Synthetic review policy approval is required.')
    for name in FIELDS-{'approval_reference'}:
        number=result[name]
        if type(number) is not int or not 0<=number<=2147483647:raise MutationError('Invalid policy '+name+'.')
    for name in ('version','supplier_id','minimum_order_units','pack_units'):
        if result[name]<1:raise MutationError('Positive '+name+' required.')
    if not 1<=result['lead_days']+result['review_days']<=7:raise MutationError('Protection horizon must fit the seven forecast days.')
    result['contract']=VERSION;result['policy_hash']=digest(result)
    return result


def policy(branch_id,sku_id):
    value=current_app.config.get('RECOMMENDATION_POLICIES',{}).get((branch_id,sku_id))
    if value is None:return None,'POLICY_NOT_CONFIGURED'
    try:return validate(value),None
    except MutationError:return None,'POLICY_INVALID_OR_UNAPPROVED'


def route(source,destination):
    value=current_app.config.get('RECOMMENDATION_ROUTES',{}).get((source,destination))
    if not isinstance(value,dict) or set(value)!={'version','transit_days','approval_reference'}:return None
    if (value['approval_reference']!=APPROVAL or type(value['version']) is not int or value['version']<1 or
        type(value['transit_days']) is not int or not 0<=value['transit_days']<=7):return None
    return {**value,'route_hash':digest(value)}
