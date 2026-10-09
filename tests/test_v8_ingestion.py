"""Use the real mechanical ingestor and immutable storage contract, no model."""
import pytest
from test_ingestion import Storage
from test_v8 import Provider, Memory, state, request, Search, source, conception
from comunicamundo_v4.hefesto.contracts import SourceInput
from comunicamundo_v4.hefesto.v8.ingestion import Ingestor
from comunicamundo_v4.hefesto.v8.provider import Calls
from comunicamundo_v4.hefesto.v8.engine import Editor
from comunicamundo_v4.hefesto.v8.contracts import identity


def setup():
    storage=Storage()
    storage.buckets['uploads']={'a.txt':b'First complete document.\n\nIts second paragraph.',
                                'b.txt':b'Second complete document.\n\nA different perspective.'}
    inputs=[SourceInput(uri='gs://uploads/'+name,title=name) for name in ('a.txt','b.txt')]
    st=state(request([x.model_dump(mode='json') for x in inputs]))
    p=Provider(conception("One coherent editorial proposal."))
    return storage,inputs,st,p,Ingestor(storage,'product',p)


@pytest.mark.asyncio
async def test_real_multi_source_adapter_does_not_collide_with_project_snapshots():
    storage,inputs,st,p,ingestor=setup()
    # Reproduce exactly the former shared project snapshot collision.
    from comunicamundo_v4.hefesto.ingestion import ingest
    from comunicamundo_v4.hefesto.contracts import EditorialInputRequest
    ingest(EditorialInputRequest(project_id=st.request.project_id,brief=st.request.brief,sources=[inputs[0]]),
           client=storage,bucket_name='product')
    old_path=f'hefesto-product/projects/{st.request.project_id}/normalized-request.json'
    original=storage.buckets['product'][old_path]
    with pytest.raises(ValueError,match='immutable object conflict: .*normalized-request.json'):
        ingest(EditorialInputRequest(project_id=st.request.project_id,brief=st.request.brief,sources=[inputs[1]]),
               client=storage,bucket_name='product')
    a=await ingestor.extract(st,inputs[0]);b=await ingestor.extract(st,inputs[1])
    assert a.source_id!=b.source_id and a.original_uri!=b.original_uri
    assert 'First complete document.' in a.text and 'Second complete document.' in b.text
    assert a.locations and b.locations and a.binary_sha256 and b.binary_sha256
    assert storage.buckets['product'][old_path]==original
    before=dict(storage.buckets['product'])
    again=await ingestor.extract(st,inputs[0])
    assert again==a and storage.buckets['product']==before
    assert not p.prompts


@pytest.mark.asyncio
async def test_real_ingestion_restart_preserves_finished_source_and_extracts_remaining():
    storage,inputs,st,p,ingestor=setup();second=storage.buckets['uploads'].pop('b.txt')
    repo=Memory();await repo.create(st);editor=Editor(repo,p,ingestor,Search(source()))
    with pytest.raises(ValueError,match='none of the supplied sources'):
        await editor.execute(st,Calls(repo,p,st))
    recovered=await repo.get(st.execution_id)
    first=recovered.sources[0].model_dump(mode='json')
    assert recovered.status=='paused' and identity(inputs[0]) in recovered.ingestion
    # Completed extraction must not be repeated during the resume.
    storage.buckets['uploads'].pop('a.txt')
    storage.buckets['uploads']['b.txt']=second
    await editor.execute(recovered,Calls(repo,p,recovered))
    assert recovered.status=='project_proposed' and len(recovered.sources)==2
    assert recovered.sources[0].model_dump(mode='json')==first
    assert len(p.prompts)==3


def test_extraction_namespace_is_stable_per_source_and_scoped_to_execution():
    storage,inputs,st,p,ingestor=setup()
    a=ingestor.request(st,inputs[0]);b=ingestor.request(st,inputs[1])
    assert a.project_id!=b.project_id and a.project_id!=st.request.project_id
    assert ingestor.request(st.model_copy(deep=True),inputs[0]).project_id==a.project_id
    other=state(st.request)
    assert ingestor.request(other,inputs[0]).project_id!=a.project_id
