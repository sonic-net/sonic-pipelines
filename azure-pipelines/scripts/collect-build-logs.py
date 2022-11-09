#!/usr/bin/python3

import datetime, time, json, os, sys
from urllib.request import Request, urlopen

# Download the web content from the url
TOKEN = ''
def get_response(url):
  for i in range(0, 3):
    try:
      req = Request(url)
      req.add_header('Authorization', TOKEN)
      req.add_header('Content-Type', 'application/json')
      response = urlopen(req)
      data=response.read()
      encoding = response.info().get_content_charset()
      return data.decode(encoding)
    except Exception as e:
      print(e)
      time.sleep(10)
  raise Exception('failed to get response from {0}'.format(url))

def get_timelines(timeline_url, build_info):
  results = []
  timeline_content =  get_response(timeline_url)
  if not timeline_content:
    return []
  records = json.loads(timeline_content)['records']
  for record in records:
    record['content'] = ""
    record['buildId'] = build_info['id']
    record['definitionId'] = build_info['definition']['id']
    record['definitionName'] = build_info['definition']['name']
    record['buildQueueTime'] = build_info['queueTime']
    record['buildStartTime'] = build_info['startTime']
    record['sourceBranch'] = build_info['sourceBranch']
    record['sourceVersion'] = build_info['sourceVersion']
    record['triggerInfo'] = build_info['triggerInfo']
    record['reason'] = build_info['reason']
    if 'parameters' in build_info:
      record['parameters'] = build_info['parameters']
    if 'status' in build_info:
      record['status'] = build_info['status']
    if 'uri' in build_info:
      record['uri'] = build_info['uri']
  return records

def get_build_logs(timelines):
  max_column_size = 104855000
  results = []
  for timeline in timelines:
    record = timeline.copy()
    if record['log']:
      log_url = record['log']['url']
      log = get_response(log_url)
      record['content'] = log[:max_column_size]
    results.append(record)
  return results

def write_logs(records, filename):
  lines = []
  if not records:
    return
  for record in records:
    lines.append(json.dumps(record))
  with open(filename, "w") as file:
    file.write('\n'.join(lines))

def main():
  global TOKEN
  argv = sys.argv[1:]
  build_url = argv[0]
  TOKEN = argv[1]
  timelinefile = argv[2]
  logfile = argv[3]
  if not(TOKEN.startswith("Bearer") or TOKEN.startswith("Basic")):
    TOKEN = "Bearer " + TOKEN # If not specified, Bearer token used
  build_content = get_response(build_url)
  build_info = json.loads(build_content)
  timeline_url = build_info['_links']['timeline']['href']
  timelines = get_timelines(timeline_url, build_info)
  logs = get_build_logs(timelines)
  write_logs(timelines, timelinefile)
  write_logs(logs, logfile)


if __name__ == "__main__":
  main()
