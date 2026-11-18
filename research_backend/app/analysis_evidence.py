"""Request-local analysis identity, causal evidence, and work accounting."""
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from inspect import signature
import json
from time import perf_counter
from types import MappingProxyType

from .causal_timing import STRICT
from .strategy_evidence import EvidenceRegistry, history_fingerprint
from .structure_pages import EventPage, StructureDelivery

CONTRACT_VERSION = 1
SCOPE_FIELDS = ("source","symbol","timeframe","feed_id","dataset_id","price_basis")
ZERO_STRUCTURE = StructureDelivery(pivot_limit=0,break_limit=0)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


class ResourceLimit(ValueError):
    """An analytical request exceeded its aggregate admission limit."""


@dataclass(frozen=True)
class AnalysisKey:
    kind: str
    scope: tuple
    history: str
    cutoff: int
    frozen: bool
    algorithm_version: int
    identity_policy_version: int
    parameters: str
    dependencies: tuple
    authority: str

    def contract(self):
        return {
            "version":CONTRACT_VERSION,"indicator":self.kind,"source_identity":dict(zip(SCOPE_FIELDS,self.scope)),
            "history":json.loads(self.history),"cutoff":self.cutoff,"frozen":self.frozen,
            "parameters":json.loads(self.parameters),"algorithm_version":self.algorithm_version,
            "identity_policy_version":self.identity_policy_version,"dependencies":list(self.dependencies),
            "authority":self.authority,
        }


class EvidenceIndex:
    """Resolve last observable evidence, with stable ordering for equal UTC evidence times."""
    def __init__(self):
        self.events=defaultdict(list)
        self.keys={}
        self.count=0

    def add(self, category, item):
        self.count+=1
        if self.count>100_000:
            raise ResourceLimit("Analytical evidence reference budget exceeded")
        self.events[category].append((item["available_at"],item["observation_time"],item["id"],item))

    def finish(self):
        for category, rows in self.events.items():
            rows.sort(key=lambda value:value[:3])
            self.keys[category]=[row[:2] for row in rows]
        return self

    def as_of(self, available, observed, *, category=None, configuration=None, source_identity=None):
        result={}
        for name, rows in self.events.items():
            if category is not None and name!=category:
                continue
            index=bisect_right(self.keys[name],(available,observed))-1
            while index>=0:
                _,stamp,_,item=rows[index]
                if (stamp<=observed and
                    ("valid_until" not in item or observed < item["valid_until"]) and
                    (configuration is None or item["configuration_fingerprint"]==configuration) and
                    (source_identity is None or item["source_identity"]==source_identity)):
                    result[name]=item
                    break
                index-=1
        return result


class ArchiveSession:
    def __init__(self,reserve=None):
        self.partitions={}
        self.counts=Counter()
        self.reserve=reserve

    def read(self, part, reader):
        key=tuple(part[field] for field in ("id","sha256","relative_path","provider","feed_id","symbol",
                                          "timeframe","price_basis","rows_count"))
        if key in self.partitions:
            self.counts["partition_reuses"]+=1
            return self.partitions[key]
        if (len(self.partitions)>=50 or self.counts["decoded_records"]+part["rows_count"]>30_000):
            raise ResourceLimit("Combined analytical archive partition or decode budget exceeded")
        if self.reserve is not None:
            self.reserve(part["rows_count"]*512)
        self.counts["checksum_attempts"]+=1
        decoded=reader(part)
        self.counts["partition_decodes"]+=1
        self.counts["decoded_records"]+=len(decoded)
        if self.counts["decoded_records"]>30_000:
            raise ResourceLimit("Combined analytical decoded records budget exceeded")
        if len(decoded)!=part["rows_count"]:
            raise ValueError("Verified partition row count mismatch")
        rows=tuple(MappingProxyType(row) for row in decoded)
        self.partitions[key]=rows
        return rows


class AnalysisCoordinator:
    def __init__(self, request, loader):
        self.request=request
        self.loader=loader
        self.reserved_bytes=0
        self.archive=ArchiveSession(self.reserve)
        self.archive_aware=loader is not None and "archive_session" in signature(loader).parameters
        self.histories={}
        self.computed={}
        self.running=set()
        self.counts=Counter()
        self.reuses=Counter()
        self.timings=Counter()
        self.work=0
        # Witnesses are side-channel evidence and must not alter public legacy events.
        self.evidence_histories = {}
        self.evidence = EvidenceRegistry(self.reserve) if request.timing_policy == STRICT else None
        self.witness=request.timing_policy==STRICT or any(s.id=="order_blocks" or s.crt and s.crt.include_context for s in request.indicators)

    def reserve(self,amount):
        if self.reserved_bytes+amount>96*1024*1024:
            raise ResourceLimit("Analytical request exceeds its estimated evidence memory budget")
        self.reserved_bytes+=amount

    def history(self,current,start,end,step,*,archive_limits=None):
        scope=tuple(getattr(current,key) for key in SCOPE_FIELDS)
        manifest=tuple(sorted((part.id,part.sha256.lower()) for part in current.partitions))
        key=(scope,manifest,current.cutoff,current.frozen,start,end,step,archive_limits)
        if key in self.histories:
            self.counts["history_reuses"]+=1
            return self.histories[key]
        slots=max(0,(end-start)//step)
        if self.counts["requested_slots"]+slots>20_000:
            raise ResourceLimit("Combined analytical request exceeds 20,000 history slots")
        self.counts["requested_slots"]+=slots
        self.counts["history_reads"]+=1
        options={} if archive_limits is None else {"archive_limits":archive_limits}
        if self.archive_aware:
            options["archive_session"]=self.archive
        result=self.loader(current,start,end,step,**options)
        self.histories[key]={**result,"rows":tuple(result["rows"])}
        actual=len(result["rows"])
        self.reserve(actual*512)
        self.counts["normalized_records"]+=actual
        if self.counts["normalized_records"]>30_000:
            raise ResourceLimit("Combined analytical normalized history budget exceeded")
        return self.histories[key]

    def run(self,kind,key,rows,calculate):
        cache_key=(kind,key)
        if cache_key in self.computed:
            self.reuses[kind]+=1
            return self.computed[cache_key]
        if cache_key in self.running:
            raise ValueError("Analytical dependency cycle")
        if sum(self.counts[k] for k in ("structure","liquidity","fvg","order_blocks","crt","levels"))>=32:
            raise ResourceLimit("Analytical calculation count budget exceeded")
        self.work+=len(rows)
        if self.work>160_000:
            raise ResourceLimit("Analytical observation work budget exceeded")
        self.reserve(len(rows)*(768 if kind=="structure" else 384))
        self.running.add(cache_key)
        self.counts[kind]+=1
        started=perf_counter()
        try:
            value=calculate()
            self.computed[cache_key]=value
            return value
        finally:
            self.timings[kind]+=round((perf_counter()-started)*1000,3)
            self.running.remove(cache_key)

    def evidence_for(self, engine, rows, provenance, configuration, *, native=()):
        if self.evidence is None:
            return None
        def digest_rows(history):
            key=id(history)
            if key not in self.evidence_histories:
                self.evidence_histories[key]=(history,history_fingerprint(history))
            return self.evidence_histories[key][1]
        source={key:value for key,value in provenance.items() if key != "retrieval"}
        return self.evidence.calculation(engine,configuration,source,
            [digest_rows(rows),digest_rows(native)],self.request.cutoff)

    def structure(self, detector, rows, step, rules, scope, snapshot_scope, delivery, consumer=None, consumer_registry=None):
        registry=self.evidence_for("structure",rows,snapshot_scope["provenance"],
            [step,rules.algorithm_version,rules.identity_parameters(),snapshot_scope["frozen"]])
        if registry is not None and consumer_registry is not None:
            consumer_registry.pin_producer("structure",registry)
        def calculate():
            observations=[]
            public=defaultdict(list)
            result=detector(rows,step,rules,scope,ZERO_STRUCTURE,snapshot_scope,
                            lambda *event:observations.append(event),local_witness=self.witness,
                            public_event=lambda kind,event:public[kind].append(event), registry=registry)
            return result,observations,public

        key=(id(rows),step,rules.algorithm_version,canonical(rules.identity_parameters()),
             canonical(scope),canonical(snapshot_scope))
        spec=self.request.indicators[0]
        if len(self.request.indicators)==1 and not (spec.crt and spec.crt.include_context or spec.ob and spec.ob.include_context):
            return self.run("structure",key,rows,lambda:detector(
                rows,step,rules,scope,delivery,snapshot_scope,consumer,local_witness=self.witness, registry=registry))
        result, observations, public=self.run("structure",key,rows,calculate)
        if consumer is not None:
            for event in observations:
                consumer(*event)
        page={"snapshot":result["event_page"]["snapshot"]}
        output={**result,"structure_warnings":list(result["structure_warnings"])}
        for name,limit,cursor in (("pivots",delivery.pivot_limit,delivery.pivot_cursor),
                                  ("breaks",delivery.break_limit,delivery.break_cursor)):
            projection=EventPage(name,delivery.limit if limit is None else limit,cursor)
            if projection.limit:
                for event in public[name]:
                    projection.append(event)
            else:
                projection.total=len(public[name])
            page[name]=projection.finish(page["snapshot"])
            output[name]=list(projection.events)
        return {**output,"event_page":page}

    def diagnostics(self, results):
        return {"version":CONTRACT_VERSION,"executions":dict(self.counts),"reuse":dict(self.reuses),
                "strategy_evidence":dict(self.evidence.counts) if self.evidence else {},
                "archive":dict(self.archive.counts),"observation_work":self.work,
                "estimated_evidence_bytes":self.reserved_bytes,
                "calculation_ms":{key:round(value,3) for key,value in self.timings.items()},
                "authorities":sorted({result.get("provenance",{}).get("context_authority","CLIENT_SUPPLIED_UNVERIFIED")
                                      for result in results}),
                "warning_count":sum(len(result["warnings"]) for result in results)}
