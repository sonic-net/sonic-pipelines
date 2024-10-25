import base64, json, time, os, re, requests

from azure.identity import AzureCliCredential
from azure.storage.queue import QueueClient

from azure.kusto.data import DataFormat
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties, FileDescriptor, ReportLevel, ReportMethod
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

# kusto query client.
def get_kusto_client():
    cluster = "https://sonic.westus2.kusto.windows.net"
    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(cluster)
    client = KustoClient(kcsb)
    return client

# kusto ingest client
def get_kusto_ingest_client():
    ingest_cluster = "https://ingest-sonic.westus2.kusto.windows.net"
    ingest_kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(ingest_cluster)
    ingest_client = QueuedIngestClient(ingest_kcsb)
    return ingest_client

# Azure Storage Queue Client
def get_queue_client(queue_name='builds', storageaccount_name='sonicazurepipelines'):
    url=f"https://{storageaccount_name}.queue.core.windows.net"
    default_credential = AzureCliCredential()
    queue_client = QueueClient(url, queue_name=queue_name ,credential=default_credential)
    return queue_client

# Download the web content from the url
def get_response(url):
    for i in range(0, 3):
        try:
            res = requests.get(url, timeout=300)
            return res.text
        except Exception as e:
            print(e)
            time.sleep(10)
    raise Exception(f'failed to get response from {url}, retry 3 times.')

def get_coverage(build_info):
    base_url = re.sub('/_apis/.*', '/_apis', build_info['url'])
    url = '{0}/test/codecoverage?buildId={1}&api-version=6.0-preview.1'.format(base_url, build_info['id'])
    coverage_content = get_response(url)
    info = json.loads(json.dumps(build_info))
    coverage = json.loads(coverage_content)
    results = []
    if 'coverageData' in coverage and len(coverage['coverageData']) > 0:
        info['coverage'] = coverage_content
        results.append(json.dumps(info))
    return results

# Get the build logs
def get_build_logs(timeline_url, build_info):
    timeline_content =  get_response(timeline_url)
    if not timeline_content:
        return []
    records = json.loads(timeline_content)['records']
    results = []
    for record in records:
        record['content'] = ""
        record['buildId'] = build_info['id']
        record['definitionId'] = build_info['definitionId']
        record['definitionName'] = build_info['definitionName']
        record['sourceBranch'] = build_info['sourceBranch']
        record['sourceVersion'] = build_info['sourceVersion']
        record['triggerInfo'] = build_info['triggerInfo']
        record['reason'] = build_info['reason']
        if record['log']:
            log_url = record['log']['url']
            log = get_response(log_url)
            lines = []
            for line in log.split('\n'):
                if '&sp=' in line and '&sig=' in line:
                    continue
                lines.append(line)
            record['content'] = '\n'.join(lines)
        if 'parameters' in build_info:
            record['parameters'] = build_info['parameters']
        if 'status' in build_info:
            record['status'] = build_info['status']
        if 'uri' in build_info:
            record['uri'] = build_info['uri']
        results.append(json.dumps(record))
    return results

def kusto_ingest(database='build', table='', mapping='', buildid='', lines=[]):
    if lines:
        tmpfile = f"{database}_{table}_{buildid}.json"
        with open(tmpfile, "w") as file:
            file.write('\n'.join(lines))
        properties = IngestionProperties(database=database, table=table, data_format=DataFormat.JSON, ingestion_mapping_reference=mapping)
        response = ingest_client.ingest_from_file(tmpfile, properties)
        print(response)
    else:
        print('No lines', database, table, buildid)

queue_client = get_queue_client()
ingest_client = get_kusto_ingest_client()

def main():
    max_messages = 30

    count = queue_client.get_queue_properties().approximate_message_count
    for page in range(0,int(count/max_messages)+1):
        messages = queue_client.receive_messages(messages_per_page=1, visibility_timeout=3600, max_messages=max_messages)
        build_messages = []
        build_infos = []
        build_logs = []
        build_coverages = []
        msgs = []
        for msg in messages:
            msgs.append(msg)
            msg_content = base64.b64decode(msg.content)
            build = json.loads(msg_content)
            content = json.dumps(build, separators=(',', ':'))
            build_messages.append(content)
            build_url = build['resource']['url']
            if 'dev.azure.com' not in build_url:
                print(f"Skipped the the url {build_url}")
                continue
            build_content = get_response(build_url)
            if not build_content:
                print("Skipped the message for no build content, the message: {}".format(msg_content))
                continue
            build_info = json.loads(build_content)
            build_info['definitionId'] = build_info['definition']['id']
            build_info['definitionName'] = build_info['definition']['name']
            build_infos.append(json.dumps(build_info))

            timeline_url = build_info['_links']['timeline']['href']
            logs = get_build_logs(timeline_url, build_info)
            build_logs += logs
            build_coverages += get_coverage(build_info)

        kusto_ingest(database='build', table='AzurePipelineBuildCoverages', mapping="AzurePipelineBuildCoverages-json", buildid=build['resource']['id'], lines=build_coverages)
        kusto_ingest(database='build', table='AzurePipelineBuildLogs', mapping="AzurePipelineBuildLogs-json", buildid=build['resource']['id'], lines=build_logs)
        kusto_ingest(database='build', table='AzurePipelineBuildMessages', mapping="AzurePipelineBuildMessages-json", buildid=build['resource']['id'], lines=build_messages)
        kusto_ingest(database='build', table='AzurePipelineBuilds', mapping="AzurePipelineBuilds-json", buildid=build['resource']['id'], lines=build_infos)
        for msg in msgs:
            print(f'deleting message: {msg.id}')
            queue_client.delete_message(msg)

if __name__ == '__main__':
    main()

# Upload a list of lines to blob
def upload_to_blob(lines, blob_prefix, file_prefix=""):
    now = datetime.datetime.now()
    if len(lines) == 0:
        return
    local_file_name = file_prefix + now.strftime("_%Y%m%d-%H%M%S-%f") + '.json'
    with open(local_file_name, "w") as file:
        count = file.write('\n'.join(lines))
    blob_file_name = blob_prefix + now.strftime("/%Y/%m/%d/") + local_file_name
    blob_client = blob_service_client.get_blob_client(container=CONTAINER, blob=blob_file_name)
    with open(local_file_name, "rb") as data:
        blob_client.upload_blob(data)
    os.remove(local_file_name)

