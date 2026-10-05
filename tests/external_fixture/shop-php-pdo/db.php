<?php
function connect() {
    $pdo = new PDO(getenv('DATABASE_URL'));
    return $pdo;
}
function report() {
    return new PDO('mysql:host=reports.internal;port=3306;dbname=reports', 'r', 'x');
}
