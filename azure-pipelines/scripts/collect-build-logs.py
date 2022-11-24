#!/usr/bin/python3

import datetime, time, json, os, sys, argparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from copy import deepcopy

TIMESTAMP = datetime.datetime.now()
TIMESTAMPSTR = TIMESTAMP.isoformat()
DEFAULT_START_TIMESTAMP = datetime.datetime.now() - datetime.timedelta(days=14)

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
    except HTTPError as e:
      if e.code == 404:
        print("404 error:", url)
        return None
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
    record['parameters'] = ''
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
    return lines
  for record in records:
    lines.append(json.dumps(record))
  if filename == "-":
    print('\n'.join(lines))
  elif filename:
    with open(filename, "w") as file:
      file.write('\n'.join(lines))

def collect_build_logs(args):
  timelines = []
  for build_id in args.buildIds.split(','):
    url = args.urlprefix + "/_apis/build/builds/" + build_id + "?api-version=7.0"
    content = get_response(url)
    build_info = json.loads(content)
    timeline_url = build_info['_links']['timeline']['href']
    timelines += get_timelines(timeline_url, build_info)
  if args.collect_build_timelines:
    write_logs(timelines, args.collect_build_timelines)
  if args.collect_build_logs:
    logs = get_build_logs(timelines)
    write_logs(logs,  args.collect_build_logs)

def get_pullrequests(args):
  from dateutil import parser as dateparser
  start_timestamp = dateparser.parse(args.start_timestamp).replace(tzinfo=None)
  results = []
  url_prefix = args.urlprefix + "/_apis/git/repositories/" + args.repository + "/pullrequests"
  url = url_prefix + "?api-version=7.0&$top=300"
  content = get_response(url + "&searchCriteria.status=completed")
  pullrequest_info = json.loads(content)
  for pullrequest in pullrequest_info['value']:
    colsedDate = dateparser.parse(pullrequest['closedDate']).replace(tzinfo=None)
    if colsedDate > start_timestamp:
      results.append(pullrequest) 
  content = get_response(url + "&searchCriteria.status=active")
  pullrequest_info = json.loads(content)
  for pullrequest in pullrequest_info['value']:
    creationDate = dateparser.parse(pullrequest['creationDate']).replace(tzinfo=None)
    if creationDate > start_timestamp:     
      results.append(pullrequest)
  return results


def collect_pullrequests(args):
  start_timestamp = args.start_timestamp
  pullrequests = get_pullrequests(args)
  url_prefix = args.urlprefix + "/_apis/git/repositories/" + args.repository + "/pullrequests"
  if not args.not_include_pullrequest_commits:
    for pullrequest in pullrequests:
      commits_url =  url_prefix + '/'  + str(pullrequest['pullRequestId']) + "/commits?api-version=7.0"
      content = get_response(commits_url)
      commits_info = json.loads(content)
      pullrequest["commits"] = commits_info['value']
      pullrequest["dump_timestamp"] = TIMESTAMPSTR
  write_logs(pullrequests,  args.collect_pullrequests)

def collect_pushes(args):
  from dateutil import parser as dateparser
  if not args.repository:
    raise Exception('The -r or --repository not specified')
  start_timestamp = dateparser.parse(args.start_timestamp).replace(tzinfo=None)
  results = []
  url_prefix = args.urlprefix + "/_apis/git/repositories/" + args.repository
  url = url_prefix + "/pushes?api-version=7.0&searchCriteria.includeRefUpdates=true&searchCriteria.fromDate={0}&$top=2000".format(start_timestamp.isoformat())
  content = get_response(url)
  push_info = json.loads(content)
  for push in push_info["value"]:
    push_id = push['pushId']
    refUpdates = push['refUpdates']
    push["commits"] = []
    push["dump_timestamp"] = TIMESTAMPSTR
    for refUpdate in refUpdates:
      tmp_push = deepcopy(push)
      tmp_push["refUpdate"] = refUpdate
      if refUpdate["newObjectId"] != "0000000000000000000000000000000000000000" and refUpdate["oldObjectId"] != "0000000000000000000000000000000000000000":
        commits_url = url_prefix + "/commits?api-version=7.0&searchCriteria.itemVersion.version={0}&searchCriteria.itemVersion.versionType=commit&searchCriteria.compareVersion.version={1}&searchCriteria.compareVersion.versionType=commit".format(refUpdate["oldObjectId"], refUpdate["newObjectId"])
        commit_content = get_response(commits_url)
        if commit_content:
          commit_info = json.loads(commit_content)
          tmp_push["commits"] = commit_info["value"]
      results.append(tmp_push)
  write_logs(results, args.collect_pushes)

def get_arguments_old():
  argv = sys.argv[1:]
  build_url = argv[0]
  arguments = [
    '-u', build_url.split('/_apis/')[0],
    '-t', argv[1],
    '-b', build_url.split('/builds/')[1].split('/')[0],
    '--collect-build-timelines', argv[2],
    '--collect-build-logs', argv[3],
  ]

  return arguments

if __name__ == "__main__":
  argv = sys.argv[1:]
  parser = argparse.ArgumentParser()
  parser.add_argument("-u", "--urlprefix", help="Azure DevOps url prefix, for example, https://dev.azure.com/mssonic/be1b070f-be15-4154-aade-b1d3bfb17054")
  parser.add_argument("-t", "--token", help="Azure DevOps token")
  parser.add_argument("-r", "--repository", help="Repository id", default="")
  parser.add_argument("-b", "--buildIds", help="Build ids, required if collecting build info")
  parser.add_argument("--collect-build-logs", help="Collect build logs")
  parser.add_argument("--collect-build-timelines", help="Collect build timelines")
  parser.add_argument("--collect-pullrequests", help="Collect pullrequests")
  parser.add_argument("--collect-pushes", help="Collect pushes")
  parser.add_argument("--not-include-pullrequest-commits", help="Not include pullrequest commits, default is false", action="store_true")
  parser.add_argument("--start-timestamp", help="The start timestamp", default=DEFAULT_START_TIMESTAMP.isoformat())
  if len(argv) > 1 and argv[0].startswith('http'): #old command line
    argv = get_arguments_old()
  args = parser.parse_args(argv)
  TOKEN = args.token
  if TOKEN and (not(TOKEN.startswith("Bearer") or TOKEN.startswith("Basic"))):
    TOKEN = "Bearer " + TOKEN # If token type not specified, Bearer token used
  if args.collect_build_timelines or args.collect_build_logs:
    collect_build_logs(args)
  if args.collect_pullrequests:
    collect_pullrequests(args)
  if args.collect_pushes:
    collect_pushes(args)
