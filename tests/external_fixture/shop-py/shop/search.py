import os
from elasticsearch import Elasticsearch

es = Elasticsearch(os.environ.get("ELASTICSEARCH_URL", "http://elastic:9200"))

def index_product(doc):
    es.index(index="shop_products", document=doc)
    es.search(index="shop_products", query={"match_all": {}})
