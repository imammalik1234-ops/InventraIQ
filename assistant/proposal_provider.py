"""Local untrusted proposal intents, separate from T25's immutable read catalog."""
from dataclasses import dataclass
import re
from security import MutationError


@dataclass(frozen=True)
class ProposalIntent:
    action: str
    arguments: dict


def fake_intent(question):
    if type(question) is not str or len(question)>500:raise MutationError('Unsupported proposal question.')
    match=re.fullmatch(r'propose write-off SKU ([1-9][0-9]{0,9}) lot ([1-9][0-9]{0,9}) quantity ([1-9][0-9]{0,9})',question)
    if match:
        sku,lot,quantity=map(int,match.groups())
        return ProposalIntent('propose_adjustment',dict(sku_id=sku,lot_id=lot,quantity=quantity,
            reason='Local assistant write-off proposal; human must verify physical disposal.'))
    match=re.fullmatch(r'propose transfer SKU ([1-9][0-9]{0,9}) quantity ([1-9][0-9]{0,9}) to branch ([1-9][0-9]{0,9}) arriving ([0-9T:+Z-]{20,32})',question)
    if match:
        sku,quantity,destination,arrival=match.groups()
        return ProposalIntent('propose_transfer',dict(sku_id=int(sku),quantity=int(quantity),
            destination_branch_id=int(destination),planned_arrival_utc=arrival,
            reason='Local assistant transfer draft proposal; ordinary independent approval remains required.'))
    raise MutationError('Unsupported proposal question. Use the exact local demonstration syntax; chat consent cannot execute an action.')
