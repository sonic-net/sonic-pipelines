#!/usr/bin/env python3

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def github_request(url, token, method='GET', payload=None):
  data = json.dumps(payload).encode('utf-8') if payload is not None else None
  request = urllib.request.Request(
    url,
    data=data,
    method=method,
    headers={
      'Accept': 'application/vnd.github+json',
      'Authorization': f'Bearer {token}',
      'Content-Type': 'application/json',
      'User-Agent': 'sonic-pipelines',
      'X-GitHub-Api-Version': '2022-11-28'
    }
  )
  with urllib.request.urlopen(request, timeout=30) as response:
    return json.load(response)


def update_branch(repository, branch, commit, token):
  if not branch or branch == 'master' or branch.startswith('refs/'):
    raise ValueError('branch must be a short, non-master branch name')
  encoded_branch = urllib.parse.quote(branch, safe='/')
  lookup_url = (
    f'https://api.github.com/repos/{repository}/git/ref/heads/{encoded_branch}'
  )
  update_url = (
    f'https://api.github.com/repos/{repository}/git/refs/heads/{encoded_branch}'
  )
  try:
    reference = github_request(lookup_url, token)
  except urllib.error.HTTPError as error:
    if error.code != 404:
      raise
    try:
      github_request(
        f'https://api.github.com/repos/{repository}/git/refs',
        token,
        method='POST',
        payload={'ref': f'refs/heads/{branch}', 'sha': commit}
      )
      return 'created'
    except urllib.error.HTTPError as create_error:
      if create_error.code != 422:
        raise
      reference = github_request(lookup_url, token)

  if reference.get('object', {}).get('sha') == commit:
    return 'unchanged'

  github_request(
    update_url,
    token,
    method='PATCH',
    payload={'sha': commit, 'force': True}
  )
  return 'updated'


def main():
  parser = argparse.ArgumentParser(
    description='Create or update a GitHub branch at a specific commit.'
  )
  parser.add_argument('--repository', required=True)
  parser.add_argument('--branch', required=True)
  parser.add_argument('--commit', required=True)
  args = parser.parse_args()

  if args.branch == 'master' or args.branch.startswith('refs/'):
    parser.error('branch must be a short, non-master branch name')
  token = os.environ.get('GH_TOKEN')
  if not token:
    parser.error('GH_TOKEN is required')

  action = update_branch(
    args.repository, args.branch, args.commit, token
  )
  print(
    f'{action.capitalize()} {args.repository}:{args.branch} at {args.commit}'
  )


if __name__ == '__main__':
  main()