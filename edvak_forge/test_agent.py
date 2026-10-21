"""Basic regression and safety tests for the synthetic dataset."""
from pathlib import Path
from agent import load_data, process_all

DATA = Path(__file__).parent / "data"

def test_all_40_messages_produce_one_result():
    data = load_data(DATA)
    results = process_all(data)
    assert len(results) == 40
    assert len({r['id'] for r in results}) == 40
    assert all({'id','patient_id','intents','urgency','actions','routed_to','tool_calls','reply_draft'} <= r.keys() for r in results)

def test_emergency_replies_are_safe():
    data = load_data(DATA)
    results = {r['id']: r for r in process_all(data)}
    for mid in ['M003','M014','M017','M032','M035']:
        r = results[mid]
        assert r['urgency'] == 'emergency'
        assert 'escalate_emergency' in r['actions']
        assert 'call 911' in r['reply_draft'].lower()
        assert 'clinic has been alerted' in r['reply_draft'].lower()

def test_no_automated_prescription_approval():
    data = load_data(DATA)
    for r in process_all(data):
        assert 'approve_refill' not in r['actions']
        assert 'bulk_approve' not in r['actions']
