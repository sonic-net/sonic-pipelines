#!/bin/bash -ex

gh --version || { curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && apt update && apt install gh -y; }

. .bashenv
echo $GH_TOKEN | gh auth login --with-token

# $1 is a single label.
check_conflict(){
    target_branch=$(echo $1 | grep -Eo [0-9]{6})
    [ -f patch ] || curl "$PR_PATCH_URL" -o patch -L
    rm -rf $REPO
    git clone https://github.com/$ORG/$REPO
    cd $REPO
    git checkout -b $target_branch
    git reset HEAD --hard
    git status
    git apply ../patch -3 || rc=$?
    cd ..
    rm -rf $REPO
    [[ "$rc" == '' ]] || gh pr edit $PR_URL --add-label "Cherry Pick Conflict_$target_branch"
    return $rc
}

create_pr(){
    [[ "$PR_MERGED" != "true" ]] && echo "PR not merged!" && return 0
    target_branch=$(echo $1 | grep -Eo [0-9]{6})
    rm -rf $REPO
    git clone https://github.com/$ORG/$REPO
    cd $REPO
    git remote add mssonicbld https://github.com/mssonicbld/$REPO
    git fetch mssonicbld
    git checkout -b $target_branch
    git cherry-pick $PR_COMMIT_SHA
    git push mssonicbld HEAD:cherry/$target_branch/$PR_NUMBER
    result=$(gh pr create -R $ORG/$REPO -H mssonicbld:cherry/$branch/${pr_id} -B $branch -t "[action] [PR:$pr_id] $title" -b '' -l "automerge" 2>&1)
    sleep 1
    echo $result | grep "already exists" && return 0 || true
    new_pr_rul=$(echo $result | grep github.com)
    gh pr comment $new_pr_rul --body "Original PR: $PR_URL"
    sleep 1
    gh pr edit $PR_URL --add-label "Created PR to $branch Branch"
    sleep 1
    gh pr comment $PR_URL --body "Cherry-pick PR to $target_branch: ${new_pr_rul}"
}

labeled(){
    echo [ AUTO CHERRY PICK ] labeled: $ACTION_LABEL
    if echo $ACTION_LABEL | grep -E '^Request for [0-9]{6} Branch$' || echo $ACTION_LABEL | grep -E '^Approved for [0-9]{6} Branch$'; then
        check_conflict "$ACTION_LABEL"
    fi

    if echo $ACTION_LABEL | grep -E '^Approved for [0-9]{6} Branch$'; then
        create_pr "$ACTION_LABEL"
    fi
}

synchronize(){
    echo [ AUTO CHERRY PICK ] synchronize: $PR_LABELS
    IFS=, read -a labels <<< $PR_LABELS
    for label in "${labels[@]}"; do
        if echo $label | grep -E '^Request for [0-9]{6} Branch$'; then
            check_conflict "$label"
        fi
    done
}

closed(){
    echo [ AUTO CHERRY PICK ] closed: $PR_LABELS
    IFS=, read -a labels <<< $PR_LABELS
    for label in "${labels[@]}"; do
        if echo $label | grep -E '^Approved for [0-9]{6} Branch$'; then
            create_pr "$label"
        fi
    done
}

$ACTION 2>error.log | tee log.log
rc=${PIPESTATUS[0]}
echo "Exit Code: $rc" >> error.log
exit $rc