#!/bin/bash -ex

. .bashenv

labeled(){
    echo [ AUTO CHERRY PICK ] labeled
    cat .bashenv
}

synchronize(){
    echo [ AUTO CHERRY PICK ] synchronize
    cat .bashenv
}

closed(){
    echo [ AUTO CHERRY PICK ] closed
    cat .bashenv
}

$ACTION
