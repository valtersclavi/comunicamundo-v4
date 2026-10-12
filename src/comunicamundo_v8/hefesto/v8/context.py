"""Versioned LLM caches for reusable corpus and editorial materials."""
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter

from .contracts import identity
from .provider import Recoverable, CapacityError

FORMAT_VERSION = "editorial-context-v2"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Contexts:
    def __init__(self, repository, provider, state):
        self.repository, self.provider, self.state = repository, provider, state

    async def retire_previous(self, key, info):
        """Retire superseded resources while preserving up to two current slots."""
        index_key=identity(["explicit-context-index"])
        index=await self.repository.operation(self.state.execution_id,index_key) or {"keys":[]}
        if key not in index["keys"]:
            index["keys"].append(key)
            await self.repository.save_operation(self.state.execution_id,index_key,index)
        for old_key in list(index["keys"]):
            if old_key==key:continue
            old=await self.repository.operation(self.state.execution_id,old_key)
            previous=old["identity"]
            changed=any(previous[v]!=info[v] for v in ("provider","model","location","provider_project","corpus_manifest","format_version"))
            changed=changed or previous.get("context_kind","corpus")!=info["context_kind"]
            replaced=previous["block_index"]==info["block_index"] and previous["block_identity"]!=info["block_identity"]
            if not changed and not replaced:continue
            if old.get("resource") and old.get("status")!="retired":
                try:await self.provider.cache_delete(old["resource"])
                except Exception as error:
                    if getattr(error,"code",None)!=404:raise
                now=datetime.now(UTC).isoformat()
                old.update(status="retired",expire_at=min(old["expire_at"],now))
                event=dict(kind="cache",cache_mode="explicit",cache_status="deleted",stage="cache-retirement",
                    resource=old["resource"],created_at=old["created_at"],expire_at=old["expire_at"],
                    token_count=old["token_count"],cache_storage_price=old["events"][-1]["cache_storage_price"],
                    **previous)
                old["events"].append(event)
                await self.repository.save_operation(self.state.execution_id,old_key,old)
            # Reconcile retirement after a restart between journal and receipt.
            if old.get("status")=="retired":
                event_id=f"{old_key}:{len(old['events'])-1}"
                if not any(v.get("cache_event_id")==event_id for v in self.state.telemetry):
                    self.state.telemetry.append(dict(old["events"][-1],cache_event_id=event_id))
                    await self.repository.save(self.state)
            index["keys"].remove(old_key)
            await self.repository.save_operation(self.state.execution_id,index_key,index)

    async def prepare(self, prompt, stage, block=None, *, use_prefix=True,
                      cache_prefix=None, cache_slot=0, cache_kind="editorial"):
        corpus = self.state.corpus
        if not hasattr(self.provider, "cache_create"):
            return None, prompt, {}
        if corpus and len(corpus.blocks) not in (1, 2):
            raise CapacityError("explicit context supports at most two corpus blocks")
        if cache_prefix is not None:
            if not cache_prefix or not prompt.startswith(cache_prefix):
                raise ValueError("cached editorial context must be an exact nonempty prompt prefix")
            if cache_slot not in (0, 1):
                raise CapacityError("explicit context supports at most two slots")
            text, block = cache_prefix, cache_slot
        else:
            if not corpus:
                return None, prompt, {}
            if block is None and use_prefix:
                block = next((i for i, text in enumerate(corpus.blocks) if prompt.startswith(text)), None)
            if block is None:
                return None, prompt, {"cache_mode": "inline", "cache_reason": "operation has no declared reusable context"}
            if block not in range(len(corpus.blocks)):
                raise CapacityError("unknown corpus block")
            text, cache_kind = corpus.blocks[block], "corpus"
            if not prompt.startswith(text):
                raise ValueError("cached corpus must be an exact prompt prefix")
        cap = await self.provider.capacity()
        for field in ("cache_min_tokens", "cache_inline_max_bytes", "cache_ttl_seconds"):
            if type(getattr(cap, field)) is not int or getattr(cap, field) <= 0:
                raise Recoverable("explicit cache requires verified " + field)
        if not cap.cache_source or cap.cache_storage_price is None or Decimal(cap.cache_storage_price) < 0:
            raise Recoverable("explicit cache requires verified limits and storage pricing")
        tokens = await self.provider.count(text)
        if tokens < cap.cache_min_tokens and text != prompt:
            # Group only material already supplied to this operation. Never pad
            # with invented text or import irrelevant sources to meet a minimum.
            text = prompt
            tokens = await self.provider.count(text)
        if tokens < cap.cache_min_tokens:
            raise Recoverable(f"Required LLM cache contains {tokens} tokens, below provider minimum "
                              f"{cap.cache_min_tokens} ({cap.cache_source}); no uncached fallback")
        info = dict(provider=self.provider.provider, model=cap.model,
                    location=self.provider.location, provider_project=self.provider.project,
                    corpus_manifest=corpus.manifest_id if corpus else None,
                    block_identity=identity(text), block_index=block,
                    context_kind=cache_kind, format_version=FORMAT_VERSION)
        # Scope includes the project execution; no cross-owner/context reuse.
        key = identity(["explicit-context", str(self.state.execution_id), info])
        record = await self.repository.operation(self.state.execution_id, key) or dict(
            kind="explicit-context", identity=info, events=[], generation=0)

        async def flush():
            await self.repository.save_operation(self.state.execution_id, key, record)
            for i, event in enumerate(record["events"]):
                event_id = f"{key}:{i}"
                if not any(v.get("cache_event_id") == event_id for v in self.state.telemetry):
                    self.state.telemetry.append(dict(event, cache_event_id=event_id))
                    await self.repository.save(self.state)

        def event(status, **values):
            record["events"].append(dict(kind="cache", cache_mode="explicit", cache_status=status,
                stage=stage, operation=key, resource=record.get("resource"),
                created_at=record.get("created_at"), expire_at=record.get("expire_at"),
                token_count=record.get("token_count"), cache_storage_price=cap.cache_storage_price,
                timestamp=datetime.now(UTC).isoformat(), latency_ms=int((perf_counter()-started)*1000),
                **info, **values))

        def account_storage(now):
            if not record.get("created_at"): return
            end = min(now, timestamp(record["expire_at"]))
            start = timestamp(record.get("storage_accounted_at", record["created_at"]))
            hours = Decimal(str(max(0, (end-start).total_seconds()))) / Decimal(3600)
            amount = Decimal(record["token_count"]) * Decimal(cap.cache_storage_price) * hours / Decimal(1000000)
            record["storage_accounted_at"] = end.isoformat()
            event("storage", cache_storage_cost_estimate_usd=str(amount), price_source=cap.price_source)

        started = perf_counter()
        try:
            await flush()  # reconcile receipts after a crash, before resource work
            now = datetime.now(UTC)
            recreate = False
            if record.get("resource"):
                if timestamp(record["expire_at"]) <= now:
                    account_storage(now)
                    event("expired")
                    record.pop("resource")
                    recreate = True
                else:
                    try:
                        remote = await self.provider.cache_get(record["resource"])
                    except Exception as error:
                        if getattr(error, "code", None) != 404: raise
                        account_storage(now)
                        event("missing")
                        record.pop("resource")
                        recreate = True
                    else:
                        if remote["model"].split("/models/")[-1] != cap.model:
                            raise Recoverable("explicit resource model mismatch")
                        if timestamp(remote["expire_at"]) <= now:
                            account_storage(now)
                            event("expired")
                            record.pop("resource")
                            recreate = True
                        else:
                            account_storage(now)
                            try:
                                remote = await self.provider.cache_renew(record["resource"], cap.cache_ttl_seconds)
                            except Exception as error:
                                if getattr(error,"code",None)!=404:raise
                                event("missing")
                                record.pop("resource")
                                recreate=True
                            else:
                                record.update(remote)
                                event("reused", cache_token_count=record["token_count"])
            if not record.get("resource"):
                # Retire replaced slots before creating a resource, including the
                # transition from two source readings to a single editorial context.
                await self.retire_previous(key, info)
                remote = None
                display = "hefesto-v8-" + key
                if record.get("creating"):
                    remote = await self.provider.cache_find(display)
                if remote is None:
                    record["creating"] = True
                    await flush()  # persisted before the costly creation request
                    remote = await self.provider.cache_create(text, display, cap)
                if (not remote.get("resource") or remote["token_count"] < cap.cache_min_tokens
                        or timestamp(remote["expire_at"]) <= datetime.now(UTC)
                        or remote["model"].split("/models/")[-1] != cap.model):
                    raise Recoverable("invalid explicit cache metadata")
                record.update(remote, creating=False, status="active", generation=record["generation"]+1)
                record["storage_accounted_at"] = remote["created_at"]
                event("recreated" if recreate or record["generation"] > 1 else "created",
                      cache_token_count=record["token_count"], input_tokens=record["token_count"],
                      cost_usd=str(Decimal(record["token_count"])*Decimal(cap.input_price)/Decimal(1000000)),
                      price_source=cap.price_source)
            record["status"] = "active"
            await flush()
            await self.retire_previous(key, info)
        except Exception as error:
            record["status"] = "error"
            event("error", error=str(error), cost_unknown=bool(record.get("creating")),
                  failed_operation="create" if record.get("creating") else "reuse")
            await flush()
            raise Recoverable("explicit context unavailable; paused without inline fallback: " + str(error)) from error
        dynamic = prompt[len(text):] if prompt.startswith(text) else prompt
        usage = dict(cache_mode="explicit", cache_status="active", cache_resource=record["resource"],
                     cache_token_count=record["token_count"], cache_expire_at=record["expire_at"],
                     cache_storage_price=cap.cache_storage_price, **info)
        return record, dynamic, usage
