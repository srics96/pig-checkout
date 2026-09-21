"""Real sandbox workload: one checkout API, local SQLite dependency, self traffic."""
import asyncio, json, logging, os, sqlite3, time, uuid
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from ddtrace import tracer
from datadog import DogStatsd

MODE = os.getenv('FAULT_MODE', 'healthy')
CATALOG_DELAY_MS = min(2000, max(0, int(os.getenv('CATALOG_DELAY_MS', '0'))))
VERSION = os.getenv('DD_VERSION', '1.0.0')
metrics = DogStatsd(host='127.0.0.1', port=8125, namespace='pig')
log_queue = asyncio.Queue(maxsize=200)
logger = logging.getLogger('pig.checkout')
logging.basicConfig(level=logging.INFO, format='%(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)

def emit(event):
    span = tracer.current_span()
    event.update(service='pig-checkout', env='sandbox', version=VERSION,
                 timestamp=int(time.time()*1000), **{'dd.trace_id':str(span.trace_id) if span else '0','dd.span_id':str(span.span_id) if span else '0'})
    logger.info(json.dumps(event))
    if os.getenv('DD_API_KEY'):
        try: log_queue.put_nowait(event)
        except asyncio.QueueFull: pass

async def ship_logs():
    async with httpx.AsyncClient(timeout=8) as client:
        while True:
            first=await log_queue.get()
            batch=[first]
            while not log_queue.empty() and len(batch)<20: batch.append(log_queue.get_nowait())
            try:
                r=await client.post('https://http-intake.logs.'+os.getenv('DD_SITE','datadoghq.com')+'/api/v2/logs',headers={'DD-API-KEY':os.environ['DD_API_KEY']},json=[{'message':json.dumps(e),'service':'pig-checkout','ddsource':'python','ddtags':'env:sandbox,project:pig','status':e.get('status','info')} for e in batch])
                if r.status_code>=300: logger.warning(json.dumps({'event':'datadog_delivery_failed','http_status':r.status_code}))
            except httpx.HTTPError: logger.warning('{"event":"datadog_delivery_failed","reason":"network"}')

async def traffic():
    await asyncio.sleep(4)
    async with httpx.AsyncClient(timeout=10) as c:
        while True:
            try: await c.post('http://127.0.0.1:8080/checkout',json={'sku':'demo-book','quantity':1})
            except httpx.HTTPError: pass
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app):
    db=sqlite3.connect('/tmp/catalog.sqlite');db.execute('CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, price INTEGER)');db.execute("INSERT OR REPLACE INTO products VALUES ('demo-book',2500)");db.commit();db.close()
    emit({'event':'service_started','dependency':'catalog.sqlite','config_version':VERSION})
    tasks=[asyncio.create_task(traffic())]
    if os.getenv('DD_API_KEY'): tasks.append(asyncio.create_task(ship_logs()))
    yield
    for t in tasks:t.cancel()

app=FastAPI(lifespan=lifespan)
@app.get('/health')
def health():return {'status':'alive','version':VERSION}
@app.post('/checkout')
async def checkout(body:dict):
    start=time.perf_counter();request_id=str(uuid.uuid4())
    tags=['env:sandbox','service:pig-checkout']
    with tracer.trace('checkout.process',service='pig-checkout',resource='POST /checkout') as span:
        try:
            with tracer.trace('catalog.lookup',service='pig-checkout'):
                # Bounded dependency-latency fixture; disabled by default.
                if CATALOG_DELAY_MS: await asyncio.sleep(CATALOG_DELAY_MS / 1000)
                # The controlled incident points at an empty catalog after a bad deployment.
                path='/tmp/catalog-v2.sqlite' if MODE=='bad_catalog' else '/tmp/catalog.sqlite'
                db=sqlite3.connect(path)
                try:row=db.execute('SELECT price FROM products WHERE sku=?',(body.get('sku','demo-book'),)).fetchone()
                finally:db.close()
                if not row:raise ValueError('Product missing')
            latency=round((time.perf_counter()-start)*1000,2)
            metrics.increment('checkout.requests',tags=tags+['outcome:success']);metrics.histogram('checkout.duration_ms',latency,tags=tags)
            emit({'event':'checkout_completed','request_id':request_id,'duration_ms':latency,'status':'info','http_status':200})
            return {'order_id':request_id,'total_cents':row[0]}
        except Exception as e:
            span.error=1;span.set_tag('error.message',str(e))
            metrics.increment('checkout.requests',tags=tags+['outcome:error'])
            emit({'event':'checkout_failed','request_id':request_id,'duration_ms':round((time.perf_counter()-start)*1000,2),'status':'error','http_status':500,'error':str(e),'dependency':'catalog','config_version':VERSION})
            raise HTTPException(500,'Checkout dependency failure')
