import base64, json, time, os, re, requests, sys

# from azure.identity import DefaultAzureCredential
from azure.identity import ManagedIdentityCredential
from azure.storage.queue import QueueClient

from azure.kusto.data import DataFormat
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder


# Check for managed identity ID
managed_identity_id = os.getenv('MANAGED_IDENTITY_ID')
if not managed_identity_id:
    print("Error: MANAGED_IDENTITY_ID environment variable is required but not found.")
    sys.exit(1)
print(f"Using managed identity ID: {managed_identity_id}")


# kusto query client.
def get_kusto_client():
    print("Initializing Kusto client...")
    cluster = "https://sonic.westus2.kusto.windows.net"
    print(f"Kusto cluster: {cluster}")
    try:
        # Use managed identity for authentication
        # credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_id)
        credential = ManagedIdentityCredential(client_id=managed_identity_id)
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(cluster, credential)
        client = KustoClient(kcsb)
        print("Kusto client initialized successfully with managed identity")
        return client
    except Exception as e:
        print(f"Failed to initialize Kusto client: {e}")
        raise


# kusto ingest client
def get_kusto_ingest_client():
    print("Initializing Kusto ingest client...")
    ingest_cluster = "https://ingest-sonic.westus2.kusto.windows.net"
    print(f"Kusto ingest cluster: {ingest_cluster}")
    try:
        # Use managed identity for authentication
        # credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_id)
        credential = ManagedIdentityCredential(client_id=managed_identity_id)
        ingest_kcsb = KustoConnectionStringBuilder.with_azure_token_credential(ingest_cluster, credential)
        ingest_client = QueuedIngestClient(ingest_kcsb)
        print("Kusto ingest client initialized successfully with managed identity")
        return ingest_client
    except Exception as e:
        print(f"Failed to initialize Kusto ingest client: {e}")
        raise


# Azure Storage Queue Client
def get_queue_client(queue_name='builds', storageaccount_name='sonicazurepipelines'):
    print("Initializing Azure Storage Queue client...")
    if os.getenv('AZURE_STORAGE_QUEUE_NAME'):
        queue_name = os.getenv('AZURE_STORAGE_QUEUE_NAME')
        print(f"Using queue name from environment variable: {queue_name}")
    else:
        print(f"Using default queue name: {queue_name}")

    url = f"https://{storageaccount_name}.queue.core.windows.net"
    print(f"Queue URL: {url}, Queue name: {queue_name}")

    try:
        # Use managed identity for authentication
        # credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_id)
        credential = ManagedIdentityCredential(client_id=managed_identity_id)
        queue_client = QueueClient(url, queue_name=queue_name, credential=credential)
        print("Azure Storage Queue client initialized successfully with managed identity")
        return queue_client
    except Exception as e:
        print(f"Failed to initialize Azure Storage Queue client: {e}")
        raise


print("Starting script initialization...")
print("Initializing clients...")
queue_client = get_queue_client()
ingest_client = get_kusto_ingest_client()
print("All clients initialized successfully")


# Download the web content from the url
def get_response(url):
    print(f"Getting response from URL: {url}")

    # Use managed identity to get Azure DevOps access token
    try:
        # credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_id)
        credential = ManagedIdentityCredential(client_id=managed_identity_id)
        # Get token for Azure DevOps - using the resource ID directly
        token_result = credential.get_token("499b84ac-1321-427f-aa17-267ca6975798")
        headers = {"Authorization": f"Bearer {token_result.token}"}
    except Exception as e:
        print(f"Failed to get managed identity token for Azure DevOps: {e}")
        raise Exception(f"Unable to authenticate with Azure DevOps: {e}")

    for attempt in range(0, 3):
        try:
            print(f"Attempt {attempt + 1}/3 for URL: {url}")
            res = requests.get(url, timeout=300, headers=headers)
            if not res.ok:
                print(f"HTTP error - Status code: {res.status_code}, Reason: {res.reason}")
                raise Exception(f'http code: {res.status_code},reason: {res.reason}')
            print(f"Successfully got response from URL: {url} (Length: {len(res.text)} chars)")
            return res.text
        except Exception as e:
            print(f"Error on attempt {attempt + 1} for URL {url}: {e}")
            if attempt < 2:  # Don't sleep on the last attempt
                time.sleep(10)
    print(f"All 3 attempts failed for URL: {url}")
    raise Exception(f'failed to get response from {url}, retry 3 times.')


def get_coverage(build_info):
    print(f"Getting coverage for build ID: {build_info.get('id', 'Unknown')}")
    try:
        base_url = re.sub('/_apis/.*', '/_apis', build_info['url'])
        url = '{0}/test/codecoverage?buildId={1}&api-version=6.0-preview.1'.format(base_url, build_info['id'])

        coverage_content = get_response(url)
        info = json.loads(json.dumps(build_info))
        coverage = json.loads(coverage_content)
        results = []

        if 'coverageData' in coverage and len(coverage['coverageData']) > 0:
            info['coverage'] = coverage_content
            results.append(json.dumps(info))

        print(f"Coverage processing completed, returning {len(results)} results")
        return results
    except Exception as e:
        print(f"Error getting coverage for build {build_info.get('id', 'Unknown')}: {e}")
        return []


# Get the build logs
def get_build_logs(timeline_url, build_info):
    print(f"Getting build logs for build ID: {build_info.get('id', 'Unknown')}.")

    try:
        timeline_content = get_response(timeline_url)
        if not timeline_content:
            print("No timeline content returned")
            return []

        records = json.loads(timeline_content)['records']
        print(f"Found {len(records)} timeline records")
        results = []

        for i, record in enumerate(records):
            record_id = record.get('id', f'record_{i}')
            print(f"Processing record {i+1}/{len(records)}: {record_id}")

            record['content'] = ""
            record['buildId'] = build_info['id']
            record['definitionId'] = build_info['definitionId']
            record['definitionName'] = build_info['definitionName']
            record['sourceBranch'] = build_info['sourceBranch']
            record['sourceVersion'] = build_info['sourceVersion']
            record['triggerInfo'] = build_info['triggerInfo']
            record['reason'] = build_info['reason']

            if record.get('log'):
                log_url = record['log']['url']
                try:
                    log = get_response(log_url)
                    lines = []
                    for line in log.split('\n'):
                        if '&sp=' in line and '&sig=' in line:
                            continue
                        lines.append(line)
                    record['content'] = '\n'.join(lines)
                    print(f"Log content loaded for record {record_id} ({len(lines)} lines)")
                except Exception as e:
                    print(f"Failed to get log content for record {record_id}: {e}")

            if 'parameters' in build_info:
                record['parameters'] = build_info['parameters']
            if 'status' in build_info:
                record['status'] = build_info['status']
            if 'uri' in build_info:
                record['uri'] = build_info['uri']

            print(f"Completed processing record: {record['id']}")
            results.append(json.dumps(record))

        print(f"Build logs processing completed, returning {len(results)} records")
        return results
    except Exception as e:
        print(f"Error getting build logs for build {build_info.get('id', 'Unknown')}: {e}")
        return []


def kusto_ingest(database='build', table='', mapping='', buildid='', lines=[]):
    print(f"Kusto ingest: database={database}, table={table}, mapping={mapping}, buildid={buildid}")
    print(f"Number of lines to ingest: {len(lines)}")

    if lines:
        tmpfile = f"{database}_{table}_{buildid}.json"
        print(f"Creating temporary file: {tmpfile}")

        try:
            with open(tmpfile, "w") as file:
                file.write('\n'.join(lines))
            print(f"Temporary file created successfully with {len(lines)} lines")

            properties = IngestionProperties(database=database, table=table, data_format=DataFormat.JSON, ingestion_mapping_reference=mapping)
            print(f"Ingestion properties created for table: {table}")

            response = ingest_client.ingest_from_file(tmpfile, properties)
            print(f"Kusto ingestion response: {response}")

            # Clean up temporary file
            if os.path.exists(tmpfile):
                os.remove(tmpfile)
                print(f"Temporary file {tmpfile} removed")

        except Exception as e:
            print(f"Error during Kusto ingestion for {table}: {e}")
            # Clean up temporary file if it exists
            if os.path.exists(tmpfile):
                os.remove(tmpfile)
                print(f"Temporary file {tmpfile} removed after error")
            raise
    else:
        print(f'No lines to ingest for: database={database}, table={table}, buildid={buildid}')


def main():
    print("=== Starting main processing ===")
    max_messages = 30
    print(f"Maximum messages per batch: {max_messages}")

    try:
        print("Getting queue properties...")
        queue_properties = queue_client.get_queue_properties()
        count = queue_properties.approximate_message_count
        print(f"Approximate message count in queue: {count}")

        if count == 0:
            print("No messages in queue, exiting")
            return

        total_pages = int(count/max_messages)+1
        # Limit processing to at most 3 pages per run
        max_pages_per_run = 3
        pages_to_process = min(total_pages, max_pages_per_run)
        print(f"Total pages available: {total_pages}, processing {pages_to_process} pages (max {max_pages_per_run} per run)")

        for page in range(0, pages_to_process):
            print(f"\n--- Processing page {page + 1}/{pages_to_process} (of {total_pages} total) ---")

            try:
                messages = queue_client.receive_messages(messages_per_page=1, visibility_timeout=3600, max_messages=max_messages)
                messages_list = list(messages)
                print(f"Received {len(messages_list)} messages from queue")

                if not messages_list:
                    print("No more messages in this batch, continuing to next page")
                    continue

                build_messages = []
                build_infos = []
                build_logs = []
                build_coverages = []
                msgs = []

                for msg_idx, msg in enumerate(messages_list):
                    print(f"\nProcessing message {msg_idx + 1}/{len(messages_list)}: {msg.id}")
                    msgs.append(msg)

                    try:
                        msg_content = base64.b64decode(msg.content)
                        build = json.loads(msg_content)
                        print(f"Decoded message content for build: {build.get('resource', {}).get('id', 'Unknown')}")

                        content = json.dumps(build, separators=(',', ':'))
                        build_messages.append(content)

                        build_url = build['resource']['url']

                        if 'dev.azure.com' not in build_url and 'msazure.visualstudio.com' not in build_url:
                            print(f"Skipped URL (not Azure DevOps): {build_url}")
                            continue

                        build_content = get_response(build_url)
                        if not build_content:
                            print(f"Skipped message for no build content, build_url: {build_url}")
                            continue

                        build_info = json.loads(build_content)
                        build_info['definitionId'] = build_info['definition']['id']
                        build_info['definitionName'] = build_info['definition']['name']
                        print(f"Build info loaded: {build_info['definitionName']} (ID: {build_info['id']})")
                        build_infos.append(json.dumps(build_info))

                        timeline_url = build_info['_links']['timeline']['href']
                        print("Getting build logs...")
                        logs = get_build_logs(timeline_url, build_info)
                        build_logs += logs
                        print(f"Added {len(logs)} log records")

                        print("Getting coverage data...")
                        coverages = get_coverage(build_info)
                        build_coverages += coverages
                        print(f"Added {len(coverages)} coverage records")

                    except Exception as e:
                        print(f"Error processing message {msg.id}: {e}")
                        continue

                # Ingest all collected data
                print(f"\n--- Ingesting data for page {page + 1} ---")
                database = 'build'
                if os.getenv('AZURE_STORAGE_DATABASE'):
                    database = os.getenv('AZURE_STORAGE_DATABASE')
                    print(f"Using database from environment: {database}")
                else:
                    print(f"Using default database: {database}")

                print(f"Summary: {len(build_coverages)} coverages, {len(build_logs)} logs, {len(build_messages)} messages, {len(build_infos)} infos")

                # Use the last processed build's ID for the buildid parameter
                last_build_id = build.get('resource', {}).get('id', 'unknown') if 'build' in locals() else 'unknown'

                try:
                    kusto_ingest(database=database, table='AzurePipelineBuildCoverages', mapping="AzurePipelineBuildCoverages-json", buildid=last_build_id, lines=build_coverages)
                    kusto_ingest(database=database, table='AzurePipelineBuildLogs', mapping="AzurePipelineBuildLogs-json", buildid=last_build_id, lines=build_logs)
                    kusto_ingest(database=database, table='AzurePipelineBuildMessages', mapping="AzurePipelineBuildMessages-json", buildid=last_build_id, lines=build_messages)
                    kusto_ingest(database=database, table='AzurePipelineBuilds', mapping="AzurePipelineBuilds-json", buildid=last_build_id, lines=build_infos)
                    print("All Kusto ingestion completed successfully")
                except Exception as e:
                    print(f"Error during Kusto ingestion: {e}")
                    raise

                # Delete processed messages
                print("Deleting processed messages...")
                for msg in msgs:
                    try:
                        print(f'Deleting message: {msg.id}')
                        queue_client.delete_message(msg)
                    except Exception as e:
                        print(f"Error deleting message {msg.id}: {e}")

                print(f"Page {page + 1} processing completed")

            except Exception as e:
                print(f"Error processing page {page + 1}: {e}")
                continue

        if total_pages > pages_to_process:
            print(f"Note: Processed {pages_to_process} pages out of {total_pages} total pages. Remaining {total_pages - pages_to_process} pages will be processed in the next run.")

        print("=== Main processing completed ===")

    except Exception as e:
        print(f"Fatal error in main(): {e}")
        raise


if __name__ == '__main__':
    print("=== Script starting ===")
    try:
        main()
        print("=== Script completed successfully ===")
    except Exception as e:
        print(f"=== Script failed with error: {e} ===")
        import traceback
        print("Full traceback:")
        traceback.print_exc()
        sys.exit(1)
