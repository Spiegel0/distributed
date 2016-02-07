
from io import BytesIO


import pytest
pytest.importorskip('fastavro')
pytest.importorskip('hdfs3')
from hdfs3 import HDFileSystem
try:
    hdfs = HDFileSystem(host='localhost', port=8020)
    hdfs.df()
    del hdfs
except:
    pytestmark = pytest.mark.skipif('True')

import fastavro
from dask.imperative import Value

from distributed.hdfs import _read_avro, avro_body, read_avro
from distributed.utils_test import gen_cluster, cluster, make_hdfs, loop
from distributed import Executor
from distributed.executor import Future


schema = {'fields': [{'name': 'key', 'type': 'string'},
          {'name': 'value', 'type': 'long'}],
          'name': 'AutoGen',
          'namespace': 'autogenerated',
          'type': 'record'}
keys = ("key%s" % s for s in range(10000))
vals = range(10000)
data = [{'key': key, 'value': val} for key, val in zip(keys, vals)]
f = BytesIO()
fastavro.writer(f, schema, data)
f.seek(0)
avro_bytes = f.read()


f.seek(0)
av = fastavro.reader(f)
header = av._header


def test_avro_body():
    sync = header['sync']
    subset = sync.join(avro_bytes.split(sync)[2:4])
    assert subset

    for b in (avro_bytes, subset):
        b = b.split(sync, 1)[1]
        L = avro_body(b, header)
        assert isinstance(L, (list, tuple))
        assert isinstance(L[0], dict)
        assert set(L[0]) == {'key', 'value'}


@gen_cluster(timeout=60)
def test_avro(s, a, b):
    e = Executor((s.ip, s.port), start=False)
    yield e._start()

    avro_files = {'/tmp/test/1.avro': avro_bytes,
                  '/tmp/test/2.avro': avro_bytes}

    with make_hdfs() as hdfs:
        for k, v in avro_files.items():
            with hdfs.open(k, 'w') as f:
                f.write(v)

            assert hdfs.info(k)['size'] > 0

        L = yield _read_avro('/tmp/test/*.avro', lazy=False)
        assert isinstance(L, list)
        assert all(isinstance(x, Future) for x in L)

        results = yield e._gather(L)
        assert all(isinstance(r, list) for r in results)
        assert results[0][:5] == data[:5]
        assert results[-1][-5:] == data[-5:]

        L = yield _read_avro('/tmp/test/*.avro', lazy=True)
        assert isinstance(L, list)
        assert all(isinstance(x, Value) for x in L)

    yield e._shutdown()


def test_avro_sync(loop):
    avro_files = {'/tmp/test/1.avro': avro_bytes,
                  '/tmp/test/2.avro': avro_bytes}
    with make_hdfs() as hdfs:
        for k, v in avro_files.items():
            with hdfs.open(k, 'w') as f:
                f.write(v)

        with cluster(nworkers=1) as (s, [a]):
            with Executor(('127.0.0.1', s['port']), loop=loop) as e:
                futures = read_avro('/tmp/test/*.avro')
                assert all(isinstance(f, Future) for f in futures)
                L = e.gather(futures)
                assert L[0][:5] == data[:5]