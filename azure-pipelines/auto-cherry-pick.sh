#!/bin/bash -ex

gh --version || { curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && apt update && apt install gh -y; }

. .bashenv
echo $GH_TOKEN | gh auth login --with-token

labeled(){
    echo [ AUTO CHERRY PICK ] labeled: $ACTION_LABEL
    echo $ACTION_LABEL | grep -E '^Request for [0-9]{6} Branch$' || { echo "label not match" && return 0; }
    target_branch=$(echo $ACTION_LABEL | grep -Eo [0-9]{6})

    echo ,$PR_LABELS, | grep -e ",Included in $target_branch Branch," -e ",Cherry Pick Conflict_$target_branch," -e ",Created PR to $target_branch Branch," && { return 0; }
    curl "$PR_PATCH_URL" -o patch -L
    git clone https://github.com/$ORG/$REPO
    cd $REPO
    git checkout -b $target_branch
    git reset HEAD --hard
    git status
    git apply ../patch -3
    rc=$?
    cd ..
    rm -rf $REPO
    return $rc
}

synchronize(){
    echo [ AUTO CHERRY PICK ] synchronize
}

closed(){
    echo [ AUTO CHERRY PICK ] closed
}

$ACTION
