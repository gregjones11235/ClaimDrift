import sys
sys.path.insert(0, '.')
from ingestion.common.elastic import ElasticsearchHttpClient
from _shared.elastic_write import DRIFT_PATTERNS_INDEX
client = ElasticsearchHttpClient()
result = client.request('DELETE', f'/{DRIFT_PATTERNS_INDEX}/_doc/$PATTERN_ID?refresh=wait_for')
print('DELETED:', result)