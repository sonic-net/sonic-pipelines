import datetime, base64, json, time, os, re, pytz, math, sys
from urllib import request
from urllib.error import HTTPError
from http.client import IncompleteRead
from dateutil import parser
import http.client

from azure.kusto.data import DataFormat
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties, FileDescriptor, ReportLevel, ReportMethod
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

GITHUB_TOKEN = sys.argv[1]
timestamp_from_blob = sys.argv[2]

ingest_cluster = "https://ingest-sonic.westus2.kusto.windows.net"
ingest_kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(ingest_cluster)
ingest_client = QueuedIngestClient(ingest_kcsb)

url="https://api.github.com/graphql"
timestamp = datetime.datetime.utcnow()
timeoffset = datetime.timedelta(minutes=5)
until = (timestamp - timeoffset).replace(tzinfo=pytz.UTC)
if 'END_TIMESTAMP' in os.environ and os.environ['END_TIMESTAMP']:
    until = parser.isoparse(os.environ['END_TIMESTAMP']).replace(tzinfo=pytz.UTC)
delta = datetime.timedelta(minutes=60)
if 'TIMEDELTA_IN_MINUTES' in os.environ and os.environ['TIMEDELTA_IN_MINUTES']:
    timedelta_in_minutes = max(int(os.environ['TIMEDELTA_IN_MINUTES']), 30)
    delta =  datetime.timedelta(minutes=timedelta_in_minutes)
max_timedelta_in_days = 35
window_in_days = 10

def kusto_ingest(database='build', table='', mapping='', lines=[]):
    now = datetime.datetime.utcnow().isoformat().replace(':','_')
    if lines:
        tmpfile = f"{database}_{table}_{now}.json"
        with open(tmpfile, "w") as file:
            file.write('\n'.join(lines))
        properties = IngestionProperties(database=database, table=table, data_format=DataFormat.JSON, ingestion_mapping_reference=mapping)
        response = ingest_client.ingest_from_file(tmpfile, properties)
        print(response)
    else:
        print('No lines', database, table, buildid)

def get_start_timestamp(force=False):
    if not force and 'START_TIMESTAMP' in os.environ and os.environ['START_TIMESTAMP']:
        return parser.isoparse(os.environ['START_TIMESTAMP']).replace(tzinfo=pytz.UTC)
    try:
        return parser.isoparse(timestamp_from_blob).replace(tzinfo=pytz.UTC)
    except (ValueError, TypeError):
        pass
    start_timestamp = datetime.datetime.utcnow() - datetime.timedelta(days=max_timedelta_in_days)
    return start_timestamp.replace(tzinfo=pytz.UTC)

def update_start_timestamp():
    if 'END_TIMESTAMP' in os.environ and os.environ['END_TIMESTAMP']:
        last = get_start_timestamp(True)
        if last > until:
            print('skipped update the start timestamp, until:%s < last:%s'.format(until.isoformat(), last.isoformat()))
            return
    return until.isoformat()

# The GitHub Graphql supports to query 100 items per page, and 10 page in max.
# To workaround it, split the query into several time range "delta", in a time range, need to make sure less than 1000 items.
def get_pullrequests():
    results = []
    start_timestamp = get_start_timestamp()
    until = min(start_timestamp + datetime.timedelta(days=window_in_days), until)
    print('start: {0}, until: {1}'.format(start_timestamp.isoformat(), until.isoformat()), flush=True)
    query_pattern = '''
    {
      search(query: "org:azure org:sonic-net is:pr updated:%s..%s sort:updated", %s type: ISSUE, first: 100) {
        issueCount
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          cursor
          node {
            ... on PullRequest {
              url
              number
              assignees (first: 10) {
                nodes {
                login
                }
              }
              title
              createdAt
              closedAt
              merged
              mergedAt
              updatedAt
              mergedBy {login}
              author {login}
              baseRefName
              baseRepository {name, url, owner{login}}
              repository {name, url, owner{login}}
              mergeCommit {id, oid, committedDate}
              commits (first: 3) {nodes{commit{oid, message}}}
              state
            }
          }
        }
      }
    }
    '''
    start = start_timestamp
    count = math.ceil((until - start) / delta)
    for index in range(count):
        end = min(start+delta, until)
        condition = ""
        while True: # pagination, support 1000 total, support 100 per page
            print("Query: index:%s, count:%s, start:%s, end:%s, page:%s" % (index, count, start.isoformat(), end.isoformat(), condition), flush=True)
            query = query_pattern %(start.isoformat(), end.isoformat(), condition)
            req = request.Request(url, method="POST")
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', "Bearer {0}".format(GITHUB_TOKEN))
            body = {}
            body['query'] = query
            data = bytes(json.dumps(body), encoding="utf-8")
            content = {}
            for i in range(10):
                try:
                    r = request.urlopen(req, data=data)
                    content = json.loads(r.read())
                    break
                except HTTPError as e:
                    print('Try count: {0}, error code: {1}, reason: {2}'.format(i, e.code, e.reason))
                    time.sleep(3)
                except IncompleteRead as e:
                    print("IncompleteRead", e)
                    time.sleep(3)
            if 'data' not in content:
                print(content)
                break
            edges = content['data']['search']['edges']
            for edge in edges:
                node = edge['node']
                node['dumpedAt'] = timestamp.isoformat()
                results.append(json.dumps(node))
            print("Read edge count: {0}, total count: {1}".format(len(results), content['data']['search']['issueCount']), flush=True)
            hasNextPage = content['data']['search']['pageInfo']['hasNextPage']
            print(content['data']['search']['pageInfo'])
            if not hasNextPage:
                break
            condition = 'after: "{0}",'.format(edges[-1]['cursor'])
            print(condition)
        start = end
    return results

results = get_pullrequests()
kusto_ingest(database='build', table='PullRequests', mapping='PullRequests-json', lines=results)
print(update_start_timestamp())
