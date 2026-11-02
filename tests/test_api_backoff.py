"""Synthetic clocks and HTTP only: persistent rate limiting never spends quota."""
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from api_backoff import Backoff, Deferred, retry_deadline, MAX_DATE
from metaculus import MetaculusClient, MetaculusError
import submit_transport as T
import submit as S
from flab.ledger import read_complete
from flab.timeutil import to_iso
from test_submit import environment, rows, NOW as SUBMIT_NOW

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
TOKEN = "synthetic-secret-not-for-ledgers"


@pytest.mark.parametrize("header,seconds", [
    ("120",120), ("0",0), (" 7 ",7),
    ("Mon, 02 Nov 2026 00:02:00 GMT",120),
    ("Monday, 02-Nov-26 00:02:00 GMT",120),
    ("Mon Nov  2 00:02:00 2026",120),
    ("Sun, 01 Nov 2026 23:59:00 GMT",0)])
def test_retry_after_seconds_and_all_http_date_formats(header, seconds):
    assert retry_deadline({"Retry-After":header}, NOW) == NOW + timedelta(seconds=seconds)


@pytest.mark.parametrize("header", [None,"", "-1","1.2","NaN",TOKEN,"１２", "10\r\nsecret: value"])
def test_invalid_headers_are_not_exposed(header):
    assert retry_deadline({"Retry-After":header}, NOW) is None


def test_huge_delay_is_not_clipped_to_an_early_retry():
    assert retry_deadline({"Retry-After":"9"*1000},NOW)==MAX_DATE


@pytest.mark.parametrize("header", ["120", "Mon, 02 Nov 2026 00:02:00 GMT"])
def test_long_cooldown_survives_new_client_without_sleep_or_network(tmp_path,header):
    clock=[NOW];calls=[];sleeps=[];path=tmp_path/'cooldowns.jsonl'
    def http(req,timeout):
        calls.append(req.get_method())
        if len(calls)==1:
            raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':header,'Authorization':TOKEN},io.BytesIO(TOKEN.encode()))
        return io.BytesIO(b'{}')
    client=MetaculusClient(TOKEN,opener=http,sleep=sleeps.append,backoff=Backoff(path,now=lambda:clock[0]))
    with pytest.raises(MetaculusError) as caught:client.get_post(1)
    assert caught.value.retry_at==to_iso(NOW+timedelta(seconds=120))
    assert calls==['GET'] and sleeps==[]
    restarted=MetaculusClient(TOKEN,opener=http,backoff=Backoff(path,now=lambda:clock[0]))
    with pytest.raises(MetaculusError):restarted.get_post(2)
    clock[0]+=timedelta(seconds=119.999)
    with pytest.raises(MetaculusError):restarted.get_post(2)
    assert calls==['GET'] and TOKEN not in path.read_text() and TOKEN not in str(caught.value)
    clock[0]+=timedelta(milliseconds=1)
    assert restarted.get_post(2)=={} and calls==['GET','GET']


def test_short_server_delay_rechecks_time_after_sleep():
    clock=[NOW];calls=[];sleeps=[]
    def http(req,timeout):
        calls.append(1)
        if len(calls)==1:raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':'7'},None)
        return io.BytesIO(b'{}')
    def sleep(seconds):sleeps.append(seconds);clock[0]+=timedelta(seconds=seconds)
    client=MetaculusClient(TOKEN,opener=http,sleep=sleep,backoff=Backoff(now=lambda:clock[0]))
    assert client.get_post(1)=={} and sleeps==[7] and len(calls)==2


def test_interrupted_sleep_cannot_issue_early_request():
    calls=[]
    def http(req,timeout):
        calls.append(1);raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':'7'},None)
    client=MetaculusClient(TOKEN,opener=http,sleep=lambda _:None,backoff=Backoff(now=lambda:NOW))
    with pytest.raises(MetaculusError):client.get_post(1)
    assert len(calls)==1


@pytest.mark.parametrize("header", [None,TOKEN])
def test_429_without_valid_header_uses_sixty_second_cooldown(tmp_path,header):
    calls=[];path=tmp_path/'cooldown.jsonl'
    def http(req,timeout):
        calls.append(1);raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':header},None)
    client=MetaculusClient(TOKEN,opener=http,max_retries=0,backoff=Backoff(path,now=lambda:NOW))
    with pytest.raises(MetaculusError):client.get_post(1)
    assert read_complete(path)[0]['not_before']==to_iso(NOW+timedelta(seconds=60))
    assert TOKEN not in path.read_text() and len(calls)==1


@pytest.mark.parametrize("status",[429,503])
@pytest.mark.parametrize("response_kind",['exception','response'])
def test_post_is_never_retried_and_blocks_reads_and_new_transports(tmp_path,status,response_kind):
    path=tmp_path/'cooldown.jsonl';calls=[]
    class Response(io.BytesIO):
        pass
    def http(req,timeout):
        calls.append(req.get_method())
        if response_kind=='exception':
            raise urllib.error.HTTPError(req.full_url,status,TOKEN,{'Retry-After':'3600'},io.BytesIO(TOKEN.encode()))
        r=Response(TOKEN.encode());r.status=status;r.headers={'Retry-After':'3600'};return r
    obj=T.APITransport(TOKEN,opener=http,backoff=Backoff(path,now=lambda:NOW));obj._bot_id=42
    with pytest.raises(S.ConfirmedFailure if status==429 else T.TransportError) as caught:
        obj.write(T.COMMENT_PATH,{'on_post':17,'text':'synthetic'},request_id='one')
    assert caught.value.retry_at==to_iso(NOW+timedelta(hours=1))
    assert calls==['POST'] and TOKEN not in str(caught.value) and TOKEN not in path.read_text()
    restarted=T.APITransport(TOKEN,opener=http,backoff=Backoff(path,now=lambda:NOW));restarted._bot_id=42
    with pytest.raises(T.TransportError):restarted.account()
    with pytest.raises(T.TransportError) as deferred:restarted.write(T.COMMENT_PATH,{'on_post':17},request_id='two')
    assert deferred.value.retry_at==to_iso(NOW+timedelta(hours=1)) and deferred.value.status==status
    assert calls==['POST']


def test_old_or_shorter_deadlines_do_not_clear_existing_cooldown(tmp_path):
    from flab.jsonl import append_record
    path=tmp_path/'cooldown.jsonl'
    for seconds in (3600,-60,10):
        append_record(path,{'not_before':to_iso(NOW+timedelta(seconds=seconds)),'http_status':429})
    guard=Backoff(path,now=lambda:NOW)
    with pytest.raises(Deferred) as exc:guard.open(lambda *_:pytest.fail('network'),None,30)
    assert exc.value.retry_at==to_iso(NOW+timedelta(hours=1))


def test_submit_journals_server_deadline_without_extra_write(environment,monkeypatch):
    e=environment;e['seed']();clock=[SUBMIT_NOW];sleeps=[]
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=e['transport'],now=lambda:clock[0],sleep=sleeps.append)
    original=e['transport'].write
    def write(path,payload,*,request_id):
        if path==S.COMMENT_PATH:
            error=S.ConfirmedFailure('HTTP 429: Too Many Requests')
            error.retry_at=to_iso(SUBMIT_NOW+timedelta(hours=1));raise error
        return original(path,payload,request_id=request_id)
    monkeypatch.setattr(e['transport'],'write',write)
    obj.run();failure=next(r for r in reversed(rows(e)) if r['status']=='confirmed_failed')
    assert failure['next_retry_at']==to_iso(SUBMIT_NOW+timedelta(hours=1))
    started=sum(r['status']=='request_started' for r in rows(e))
    obj.run()
    assert sum(r['status']=='request_started' for r in rows(e))==started and sleeps==[]


def test_safe_read_transport_preserves_delay_and_waits_for_clock():
    clock=[NOW];calls=[]
    def http(req,timeout):
        calls.append(1)
        if len(calls)==1:raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':'7'},None)
        return io.BytesIO(json.dumps({'id':42,'is_bot':True}).encode())
    obj=T.APITransport(TOKEN,opener=http,backoff=Backoff(now=lambda:clock[0]))
    def sleep(seconds):clock[0]+=timedelta(seconds=seconds)
    obj.client._sleep=sleep
    assert obj.account()=={'id':42,'is_bot':True} and clock[0]==NOW+timedelta(seconds=7)
    assert len(calls)==2


@pytest.mark.parametrize('content',['{', '{}\n', '{"not_before":"invalid","http_status":429}\n'])
def test_corrupt_ledger_fails_before_network(tmp_path,content):
    path=tmp_path/'cooldown.jsonl';path.write_text(content)
    guard=Backoff(path,now=lambda:NOW)
    with pytest.raises((ValueError,KeyError)):
        guard.open(lambda *_:pytest.fail('network'),None,30)


def test_production_clients_share_default_ledger():
    from api_backoff import DEFAULT_PATH
    client=MetaculusClient(TOKEN);transport=T.APITransport(TOKEN)
    assert client._backoff.path==transport._backoff.path==DEFAULT_PATH
    assert transport.client._backoff is transport._backoff


def test_two_clients_serialize_through_rate_limit_response(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    path=tmp_path/'cooldown.jsonl';entered=threading.Event();release=threading.Event();second=threading.Event();calls=[]
    from urllib.request import Request
    req=Request('https://www.metaculus.com/api/users/me/')
    def http(req,timeout):
        calls.append(1);entered.set();assert release.wait(2)
        raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':'120'},None)
    def run(guard,is_second=False):
        if is_second:second.set()
        try:guard.open(http,req,30)
        except Exception as exc:return type(exc).__name__
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(run,Backoff(path,now=lambda:NOW));assert entered.wait(2)
        b=pool.submit(run,Backoff(path,now=lambda:NOW),True);assert second.wait(2)
        release.set()
        assert a.result()=='HTTPError' and b.result()=='Deferred'
    assert len(calls)==1 and len(read_complete(path))==1


@pytest.mark.parametrize('status',[TOKEN,True,999])
def test_invalid_error_status_never_reaches_cooldown_ledger(tmp_path,status):
    path=tmp_path/'cooldown.jsonl'
    def http(req,timeout):raise urllib.error.HTTPError(req.full_url,status,TOKEN,{'Retry-After':'120'},None)
    obj=T.APITransport(TOKEN,opener=http,backoff=Backoff(path,now=lambda:NOW));obj._bot_id=42
    with pytest.raises(T.TransportError) as exc:obj.write(T.COMMENT_PATH,{'on_post':17},request_id='one')
    assert TOKEN not in str(exc.value) and not path.exists()


@pytest.mark.parametrize('header',['0','Sun, 01 Nov 2026 23:59:00 GMT'])
def test_zero_or_past_429_header_gets_future_fallback(tmp_path,header):
    path=tmp_path/'cooldown.jsonl';calls=[]
    def http(req,timeout):
        calls.append(1);raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':header},None)
    client=MetaculusClient(TOKEN,opener=http,sleep=lambda _:None,backoff=Backoff(path,now=lambda:NOW))
    with pytest.raises(MetaculusError):client.get_post(1)
    assert len(calls)==1 and read_complete(path)[0]['not_before']==to_iso(NOW+timedelta(seconds=60))


def test_submillisecond_rounding_defers_safely_instead_of_clipping(tmp_path):
    clock=[NOW+timedelta(microseconds=123)];path=tmp_path/'cooldown.jsonl';calls=[];sleeps=[]
    def http(req,timeout):
        calls.append(1)
        if len(calls)==1:raise urllib.error.HTTPError(req.full_url,429,TOKEN,{'Retry-After':'60'},None)
        return io.BytesIO(b'{}')
    client=MetaculusClient(TOKEN,opener=http,sleep=sleeps.append,backoff=Backoff(path,now=lambda:clock[0]))
    with pytest.raises(MetaculusError) as exc:client.get_post(1)
    assert exc.value.retry_at==to_iso(NOW+timedelta(seconds=60,milliseconds=1)) and sleeps==[]
    clock[0]+=timedelta(seconds=60)
    with pytest.raises(MetaculusError):client.get_post(1)
    assert len(calls)==1
    clock[0]=NOW+timedelta(seconds=60,milliseconds=1)
    assert client.get_post(1)=={} and len(calls)==2


def test_zero_retry_after_5xx_keeps_local_exponential_delay():
    clock=[NOW];calls=[];sleeps=[]
    def http(req,timeout):
        calls.append(1)
        if len(calls)<=2:raise urllib.error.HTTPError(req.full_url,503,TOKEN,{'Retry-After':'0'},None)
        return io.BytesIO(b'{}')
    def sleep(seconds):sleeps.append(seconds);clock[0]+=timedelta(seconds=seconds)
    client=MetaculusClient(TOKEN,opener=http,sleep=sleep,backoff=Backoff(now=lambda:clock[0]))
    assert client.get_post(1)=={} and sleeps==[1,2] and len(calls)==3
