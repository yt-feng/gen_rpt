"""Source-pinned news maintenance and truthful publication completion, offline."""
import contextlib,importlib.util,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('news_runner',Path(__file__).resolve().parents[1]/'scripts/gatex_localization_runner.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
A,B='a'*64,'b'*64


def state(ready=0,total=36,digest=A,promoted=False):
    base={'ok':True,'version':'reviewed-v2','snapshotHash':digest,'total':total,'ready':ready,'pending':total-ready,'failed':0,'complete':ready==total and total>0 and promoted}
    return {**base,'candidate':{**base,'complete':ready==total and total>0,'fixed':True},
        'published':{'available':promoted,'complete':promoted,'snapshotHash':digest if promoted else None,'total':total if promoted else 0,'ready':ready if promoted else 0},
        'records':[{'summary':'SOURCE BODY MUST NOT ENTER QA'}]}


class NewsMaintenanceTests(unittest.TestCase):
    def api(self,request):
        api=runner.NewsfeedApi('test-token',sleep=lambda _:None);api.request=request;return api

    def run_news(self,request,**kwargs):
        return runner.warm_news(self.api(request),require_publication=True,**kwargs)

    def test_complete_fixed_edition_never_selects_new_sources_or_warms_again(self):
        calls=[]
        def request(method,route,**kwargs):
            calls.append((method,route,kwargs.get('json')))
            return state(36,promoted=True)
        report=self.run_news(request)
        self.assertEqual(report['status'],'passed');self.assertEqual(report['expectedSnapshotHash'],A)
        self.assertEqual(calls,[('GET','/status',None)])
        self.assertEqual(report['warmPasses'],0)

    def test_fixed_candidate_progress_is_resumed_without_replacing_source_batch(self):
        calls=[];current=state(17)
        def request(method,route,**kwargs):
            nonlocal current
            calls.append((method,route,kwargs.get('json')))
            if method=='POST':current=state(25) if current['ready']==17 else state(36,promoted=True)
            return current
        report=self.run_news(request)
        self.assertEqual(report['status'],'passed');self.assertEqual(report['warmPasses'],2)
        self.assertEqual([body for method,_,body in calls if method=='POST'],[{'expectedSnapshotHash':A}, {'expectedSnapshotHash':A}])

    def test_cache_complete_without_confirmed_same_publication_cannot_pass(self):
        for published in ({'available':False,'complete':False,'snapshotHash':None,'total':0,'ready':0},
            {'available':True,'complete':True,'snapshotHash':B,'total':36,'ready':36}):
            current=state(36,promoted=True);current['published']=published
            with self.subTest(published=published):
                report=self.run_news(lambda *args,**kwargs:current,max_passes=1)
                self.assertEqual(report['status'],'failed');self.assertEqual(report['stopReason'],'MAX_PASSES')

    def test_wrong_snapshot_never_resets_pin_or_sends_a_warm_to_another_batch(self):
        calls=[];posts=0
        def request(method,route,**kwargs):
            nonlocal posts
            calls.append((method,route,kwargs.get('json')))
            if method=='POST':
                posts+=1
                return state(4) if posts==1 else state(12,digest=B)
            return state(4)
        report=self.run_news(request)
        self.assertEqual(report['status'],'failed');self.assertEqual(report['stopReason'],'SNAPSHOT_CHANGED')
        self.assertEqual(report['expectedSnapshotHash'],A);self.assertEqual(report['ready'],4)
        self.assertEqual([body for method,_,body in calls if method=='POST'],[{'expectedSnapshotHash':A}, {'expectedSnapshotHash':A}])

    def test_http409_reports_pin_conflict_without_retrying_paid_work(self):
        posts=0
        def request(method,route,**kwargs):
            nonlocal posts
            if method=='POST':
                posts+=1
                if posts>1:raise runner.RunnerError('API_HTTP_ERROR',409,'NEWS_SNAPSHOT_CHANGED')
            return state(4)
        report=self.run_news(request)
        self.assertEqual(report['stopReason'],'SNAPSHOT_CHANGED');self.assertEqual(posts,2)
        self.assertEqual(report['httpStatus'],409);self.assertEqual(report['apiCode'],'NEWS_SNAPSHOT_CHANGED')

    def test_unknown_post_outcome_is_read_before_retry_and_recovers_completed_promotion(self):
        calls=[];completed=False
        def request(method,route,**kwargs):
            nonlocal completed
            calls.append((method,route,kwargs.get('json')))
            if method=='POST':
                completed=True;raise runner.RunnerError('API_CONNECTION_FAILED')
            return state(36,promoted=True) if completed else state(17)
        report=self.run_news(request)
        self.assertEqual(report['status'],'passed');self.assertEqual(report['warmPasses'],1)
        self.assertEqual(calls,[('GET','/status',None),('POST','/warm',{'expectedSnapshotHash':A}),('GET','/status?expectedSnapshotHash='+A,None)])

    def test_unknown_pinned_post_outcome_uses_pinned_status_read(self):
        calls=[];posts=0
        def request(method,route,**kwargs):
            nonlocal posts
            calls.append((method,route,kwargs.get('json')))
            if method=='POST':
                posts+=1
                if posts==2:raise runner.RunnerError('API_CONNECTION_FAILED')
            return state(36,promoted=True) if posts==2 else state(4)
        report=self.run_news(request)
        self.assertEqual(report['status'],'passed');self.assertEqual(report['warmPasses'],2)
        self.assertEqual(calls[-1],('GET','/status?expectedSnapshotHash='+A,None))

    def test_source_unchanged_complete_edition_confirms_without_extra_provider_loop(self):
        calls=[]
        def request(method,route,**kwargs):
            calls.append((method,route,kwargs.get('json')));return state(36,promoted=True)
        report=self.run_news(request)
        self.assertEqual(report['status'],'passed');self.assertEqual(report['warmPasses'],0)
        self.assertEqual(calls,[('GET','/status',None)])

    def test_current_server_contract_is_required_before_any_warm(self):
        old=state(36,promoted=True);old.pop('candidate');old.pop('published');calls=[]
        def request(method,route,**kwargs):calls.append(method);return old
        with self.assertRaises(runner.RunnerError) as caught:self.run_news(request)
        self.assertEqual(caught.exception.code,'NEWS_PUBLICATION_CONTRACT_REQUIRED');self.assertEqual(calls,['GET'])

    def test_19_pending_fails_cli_and_preserves_safe_counts(self):
        current=state(17);api=self.api(lambda *args,**kwargs:current)
        with tempfile.TemporaryDirectory() as directory,patch.object(runner,'NewsfeedApi',return_value=api),\
            patch.object(sys,'argv',['runner','--news-only','--runtime',directory,'--summary',directory+'/summary.json']),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),1);report=json.loads((Path(directory)/'summary.json').read_text())
        self.assertEqual(report['status'],'failed');self.assertEqual(report['pending'],19)
        self.assertEqual(report['expectedSnapshotHash'],A);self.assertEqual(report['stopReason'],'NO_PROGRESS')
        self.assertNotIn('SOURCE BODY',json.dumps(report))

    def test_permanent_api_failure_preserves_last_known_pending_counts(self):
        def request(method,route,**kwargs):
            if method=='POST':raise runner.RunnerError('API_HTTP_ERROR',401,'AUTH_REJECTED')
            return state(17)
        report=self.run_news(request)
        self.assertEqual(report['status'],'failed');self.assertEqual(report['pending'],19)
        self.assertEqual(report['stopReason'],'API_FAILURE');self.assertEqual(report['httpStatus'],401)

    def test_malformed_publication_proof_cannot_echo_source_text(self):
        current=state(36,promoted=True);current['published']['snapshotHash']='SOURCE BODY'
        with self.assertRaises(runner.RunnerError) as caught:self.run_news(lambda *args,**kwargs:current)
        self.assertEqual(caught.exception.code,'INVALID_NEWS_PUBLICATION_STATUS')


if __name__=='__main__':unittest.main()
