import traceback

from defs import USE_POLYGON, QUOTE_PREFIX, MessageType, TRADE_PREFIX, \
    MINUTE_AGG_PREFIX, SECOND_AGG_PREFIX, reverse_polygon_qoute_mapping, \
    reverse_qoute_mapping, reverse_polygon_trade_mapping, \
    reverse_trade_mapping, reverse_polygon_aggs_mapping, \
    reverse_minute_agg_mapping
from shared_memory_obj import subscribers, response_queue

from websockets.protocol import State


def _get_correct_entity_mapping(m, _type):
    """
    we may handle different message types (aggs, quotes, trades)
    this method decide what reverese mapping to use
    :return:
    """
    if _type == MessageType.Quote:
        stream = 'Q' if USE_POLYGON else f"Q.{m.symbol}"
        _mapping = reverse_polygon_qoute_mapping if USE_POLYGON else \
            reverse_qoute_mapping
    elif _type == MessageType.Trade:
        stream = 'T' if USE_POLYGON else f"T.{m.symbol}"
        _mapping = reverse_polygon_trade_mapping if USE_POLYGON else \
            reverse_trade_mapping
    elif _type == MessageType.MinuteAgg:
        stream = 'AM' if USE_POLYGON else f"AM.{m.symbol}"
        _mapping = reverse_polygon_aggs_mapping if USE_POLYGON else \
            reverse_minute_agg_mapping
    elif _type == MessageType.SecondAgg:
        # only supported in polygon
        stream = 'A'
        _mapping = reverse_polygon_aggs_mapping
    return stream, _mapping


def _build_restructured_message(m, _type: MessageType):
    """
    the sdk translate the message received from the server to a more
    readable format. so this is how we get it (readable). but when we pass
    it to the clients using this proxy, the clients expects the message to
    be not readable (or, server compact), and tries to translate it to
    readable format. so this method converts it back to the expected format
    :param m:
    :return:
    """
    stream, _mapping = _get_correct_entity_mapping(m, _type)

    # polygon and alpaca message structure is different
    if USE_POLYGON:
        data = {_mapping[k]: v for
                k, v in m._raw.items() if
                k in _mapping}
        data['ev'] = stream
        data['sym'] = m.symbol
        message = [data]
    else:
        message = {
            'stream': stream,
            'data':   {_mapping[k]: v for k, v in
                       m._raw.items() if k in _mapping}
        }
    return message


def _get_original_message(msg, chans):
    """
    This method tries to translate the Entity to its original minimized server
    The reason is - the clients expects the server format and can't handle the
    translated message.
    We use the same mapping used by the python SDK and then try to verify it
    by making sure some of the fields exist in the re-constructed message
    :param msg:
    :param chans:
    :return:
    """
    restructured = None
    if QUOTE_PREFIX + msg.symbol in chans or QUOTE_PREFIX + "*" in chans:
        restructured = _build_restructured_message(msg, MessageType.Quote)
        if USE_POLYGON:
            first = restructured[0]
            if 'bs' not in first or 'bp' not in first or 'as' not in first:
                restructured = None
        else:
            if 'data' not in restructured:
                restructured = None
            elif 'x' not in restructured['data'] \
                    or 'p' not in restructured['data'] \
                    or 's' not in restructured['data']:
                restructured = None

    if not restructured and (TRADE_PREFIX + msg.symbol in
                             chans or TRADE_PREFIX + "*" in chans):
        restructured = _build_restructured_message(msg, MessageType.Trade)
        if USE_POLYGON:
            first = restructured[0]
            if 'x' not in first or 'p' not in first or 's' not in first:
                restructured = None
        else:
            if 'data' not in restructured:
                restructured = None
            elif 'x' not in restructured['data'] \
                    or 'p' not in restructured['data'] \
                    or 's' not in restructured['data']:
                restructured = None
    if not restructured and \
            (MINUTE_AGG_PREFIX + msg.symbol in chans or
             [l for l in chans if MINUTE_AGG_PREFIX + "*" in l]):
        restructured = _build_restructured_message(msg, MessageType.MinuteAgg)
        if USE_POLYGON:
            first = restructured[0]
            if 'o' not in first or 'h' not in first or 'l' not in first:
                restructured = None
        else:
            if 'data' not in restructured:
                restructured = None
            elif 'o' not in restructured['data'] or \
                    'h' not in restructured['data'] or \
                    'l' not in restructured['data']:
                restructured = None
    if not restructured and (SECOND_AGG_PREFIX + msg.symbol in chans or
                             SECOND_AGG_PREFIX + "*" in chans):
        restructured = _build_restructured_message(msg, MessageType.SecondAgg)
        first = restructured[0]
        if 'o' not in first or 'h' not in first or 'l' not in first:
            restructured = None
    return restructured


async def on_message(conn, subject, msg):
    """
    This is the handler for server messages.
    The entry point to this module.
    We get the server message, already as Entity form translated by the python
    SDK. We try to bring it back to raw form, and then send it to every client
    registered to this service.

    The process of re-constructing is not optimal and we may do it for more
    than one client even if we already have it re-constructed.
    There's some refactoring required to make sure we re-construct once.

    :param conn:
    :param subject:
    :param msg:
    :return:
    """
    # iterate channels and distribute the message to correct subscribers
    try:
        for sub, channels in subscribers.items():
            restructured = _get_original_message(msg, channels)

            if sub.state != State.CLOSED:
                if restructured:
                    # only if we were able to restructure it
                    response_queue.put({"subscriber": sub,
                                        "response":   restructured})
    except Exception as e:
        print(e)
        traceback.print_exc()
