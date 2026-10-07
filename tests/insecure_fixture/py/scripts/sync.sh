#!/bin/sh
# StrictHostKeyChecking=no in a comment is not a hit
rsync -e "ssh -o StrictHostKeyChecking=no" ./out stock@warehouse.bookstore.example:/srv/stock
rsync -e "ssh -o StrictHostKeyChecking=yes" ./out stock@ledger.bookstore.example:/srv/ledger
