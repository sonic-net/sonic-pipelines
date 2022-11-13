#!/usr/bin/python3

# Collect the commits info

import datetime, time, json, os, sys, git
from dateutil import parser

MAX_COMMIT_COUNT_IN_BRANCH=int(os.getenv('MAX_COMMIT_COUNT_IN_BRANCH', 200))
TIMESTAMP = datetime.datetime.now()
TIMESTAMPSTR = TIMESTAMP.isoformat()
DEFAULT_MIN_COMMIT_TIMESTAMP = datetime.datetime.now() - datetime.timedelta(days=180)
MIN_COMMIT_TIMESTAMP =parser.parse(os.getenv('MIN_COMMIT_TIMESTAMP', DEFAULT_MIN_COMMIT_TIMESTAMP.isoformat())).replace(tzinfo=None)

def collect_commit(commit, tagNames):
  result = {
    'hexsha': commit.hexsha,
    'author': {'name':commit.author.name, 'email':commit.author.email},
    'authored_datetime': commit.authored_datetime.isoformat(),
    'committer': {'name':commit.committer.name, 'email': commit.committer.email},
    'committed_datetime': commit.committed_datetime.isoformat(),
    'message': commit.message,
    'summary': commit.summary,
    'files': commit.stats.files,
    'dump_timestamp': TIMESTAMPSTR,
    'tree_hexsha': commit.tree.hexsha,
  }
  return result

def collect_commits(repoPath, branches):
  repo = git.Repo(repoPath)
  results = []
  tag_commits = {}
  repo_url = repo.remotes.origin.url
  repo_name = repo_url.split('.git')[0].split('/')[-1]
  for tag in repo.tags:
    tagNames = [tag.name]
    if tag.commit.hexsha in tag_commits:
      tag_commits[tag.commit.hexsha] += tagNames
    else:
      tag_commits[tag.commit.hexsha] = tagNames
  for branch in branches:
    commits = list(repo.iter_commits(branch, max_count=MAX_COMMIT_COUNT_IN_BRANCH))
    for commit in commits:
      if commit.committed_datetime.replace(tzinfo=None) < MIN_COMMIT_TIMESTAMP:
        continue
      tagNames = tag_commits.get(commit.hexsha, [])
      format_commit = collect_commit(commit, tagNames)
      format_commit['branch'] = branch
      format_commit['tags'] = tagNames
      format_commit['repo_name'] = repo_name
      format_commit['repo_url'] = repo_url
      results.append(format_commit)
  return results

def records_tostring(records):
  lines = []
  if not records:
    return lines
  for record in records:
    lines.append(json.dumps(record))
  return '\n'.join(lines)
  with open(filename, "w") as file:
    file.write('\n'.join(lines))

def main():
  argv = sys.argv[1:]
  repoPath = argv[0]
  branches = argv[1].split(',')
  commits = collect_commits(repoPath, branches)
  commits_str = records_tostring(commits)
  if len(argv) >= 3:
    filename = argv[2]
    with open(filename, "w") as file:
      file.write(commits_str)
  else:
    print(commits_str)

if __name__ == "__main__":
  main()
