from datetime import UTC, datetime
import os
import json


def external(stage,provider,latency_ms,source_ids=(),error=None):
    """API request prices can depend on account plans; never claim unknown is zero."""
    prices=json.loads(os.environ.get("HEFESTO_V8_EXTERNAL_PRICES","{}"))
    price=prices.get(provider, "0" if provider=="direct" else None)
    return dict(stage=stage,provider=provider,model=None,prompt_version=None,
        source_ids=list(map(str,source_ids)),input_tokens=0,output_tokens=0,cached_tokens=0,
        latency_ms=latency_ms,cost_usd=str(price) if price is not None else "0",
        cost_unknown=price is None,cost_basis="configured USD per request" if price is not None else "account API tariff unavailable",
        status="failed" if error else "complete",error=error,
        called_at=datetime.now(UTC).isoformat())
