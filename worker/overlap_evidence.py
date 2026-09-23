"""One private-catalog worker; evidence and its exact handoff stay in the parent."""
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
import hashlib
import json
import multiprocessing
from pathlib import Path
import time


def player_scope(rows):
    result={}
    for row in rows:
        key=row['objectId']
        value=(row['characterCode'],row['teamNumber'])
        if key in result:raise ValueError('Duplicate player identity in evidence scope')
        result[key]=value
    if not result:raise ValueError('Empty player scope is not a successful analysis')
    return result


def assert_same_scope(expected,private):
    if expected!=player_scope(private['players']):
        raise ValueError('Early evidence player scope differs from validated combat result')


def input_version_context(version):
    if version=='12.3.0':return nullcontext()
    if version!='12.4.0':raise ValueError('Unsupported exact skill database revision')
    from decoder.replay_input_context import exact_input_version
    return exact_input_version(version)


def collect_evidence(raw,database,decode_path,version,scope,layout):
    with input_version_context(version):
        return _collect_evidence(raw,database,decode_path,version,scope,layout)


def _collect_evidence(raw,database,decode_path,version,scope,layout):
    # This runs in the original job process: never serialize the memory handoff.
    from decoder.audit_projectile_runtime_replay import build_runtime_audit
    from decoder.corpus_runtime_source import CorpusRuntimeSource
    from decoder.requested_skill_scope import active_manifest,implementation_fingerprint
    from decoder.skill_game_data_contract import validate_archive
    from gc_metrics import large_graph_threshold
    started=time.perf_counter()
    implementation_fingerprint()
    validate_archive(database,version)
    raw=Path(raw);sha=hashlib.sha256(raw.read_bytes()).hexdigest()
    CorpusRuntimeSource(decode_path,sha,version)
    evidence={};identity={}
    with large_graph_threshold():
        audit=build_runtime_audit(raw,Path(database),
            verified_prefix_layout={int(k):v for k,v in layout.items()},
            prefix_layout_authority='multi-match-exact-prefix-consensus-v1',
            full_decode_path=Path(decode_path),include_full_requested_skill_metrics=True,
            calculate_full_requested_skill_metrics=False,calculate_projectile_metrics=False,
            requested_metric_specs=[s for s in active_manifest() if s['characterCode'] in {v[0] for v in scope.values()}],
            skill_scope_evidence_result_out=evidence,skill_scope_identity_result_out=identity)
    return dict(audit=audit,evidence=evidence,identity=identity,sha=sha,scope=scope,version=version,
                seconds=time.perf_counter()-started)


def collect_private_catalog(config,options,version):
    """No acquisition or login state is sent to this bounded child process."""
    with input_version_context(version):
        from build_combat_analysis import run_analysis
        from gc_metrics import large_graph_threshold
        timings={}
        with large_graph_threshold():
            catalog=run_analysis(config,timing_sink=timings,**options)
    return catalog,timings


class JobEvidence:
    def __init__(self,resources,record,core):
        self.resources=resources;self.record=record;self.core=core;self.future=None;self.started=False;self.result=None

    def run(self,config,*args,**options):
        if args:raise TypeError('Private analysis options must be named')
        if self.started:raise RuntimeError('Evidence worker already started for this job')
        self.started=True
        from decoder.corpus_runtime_source import CorpusRuntimeSource
        from decoder.projectile_prefix_consensus import validate_projectile_prefix_consensus
        inspect=json.loads(config.inspect_path.read_bytes())
        self.scope=player_scope(inspect['playerSummary'])
        version=inspect['format']['clientVersion']
        if version=='12.3.0':consensus_path='tmp/targeted-v75-run/prefix-consensus.json'
        elif version=='12.4.0':consensus_path='schema/projectile-prefix-consensus-12.4.json'
        else:raise ValueError('Unsupported exact skill database revision')
        consensus=json.loads((self.core/consensus_path).read_bytes())
        validate_projectile_prefix_consensus(consensus)
        matching=[v for v in consensus['versions'] if v['clientVersion']==version]
        if len(matching)!=1:raise ValueError('Expected exactly one matching prefix consensus version')
        layout=matching[0]
        if version=='12.4.0':
            minimum=layout.get('minimumMatches')
            report_minimum=consensus.get('minimumMatchesPerObjectType')
            count=layout.get('exactCandidateMatchCount')
            rows=layout.get('objectTypes')
            if (
                layout.get('status')!='candidate-layout-consensus-ready-for-explicit-version-promotion'
                or type(minimum) is not int or minimum<3
                or type(report_minimum) is not int or report_minimum<3
                or type(count) is not int or count<max(minimum,report_minimum)
                or not layout.get('candidateLayout') or not rows
                or any(row.get('status')!='candidate-layout-consensus-ready-for-explicit-review'
                       or type(row.get('supportingMatchCount')) is not int
                       or row['supportingMatchCount']<max(minimum,report_minimum) for row in rows)
            ):raise ValueError('12.4 prefix consensus requires ready evidence from at least three matches')
        # Enter after acquisition so this executor joins before raw-input cleanup.
        executor=self.resources.enter_context(ProcessPoolExecutor(max_workers=1,
                      mp_context=multiprocessing.get_context('spawn')))
        self.executor=executor
        self.database=config.game_data_path
        timing_sink=options.pop('timing_sink',None)
        self.future=executor.submit(collect_private_catalog,config,options,version)
        try:
            self.result=collect_evidence(str(config.replay_path),str(self.database),
                str(CorpusRuntimeSource.path_from_acquisition(self.record)),version,self.scope,layout['candidateLayout'])
            catalog,timings=self.future.result()
        finally:
            executor.shutdown(wait=True)
            self.future=None
        if timing_sink is not None:timing_sink.update(timings)
        return catalog

    def finish(self,private,folder):
        if self.result is None:raise RuntimeError('Evidence collection did not complete')
        assert_same_scope(self.scope,private)
        result=self.result
        self.result=None
        assert_same_scope(result['scope'],private)
        if result['version']!=private['meta']['clientVersion']:
            raise ValueError('Early evidence version differs from validated combat result')
        (folder/'overlap-timings.json').write_text(json.dumps({
            'evidenceWorkerSeconds':round(result['seconds'],6),
            'sameMatchJobs':1,'privateCatalogWorkers':1,'evidenceHandoffProcessLocal':True,
            'validatedPlayerScope':True}),encoding='utf8')
        return result
