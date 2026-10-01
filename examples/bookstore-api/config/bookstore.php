<?php

return [
    // connection used by the old warehouse system (read-only mirror of its stock table)
    'warehouse_connection' => env('BOOKSTORE_WAREHOUSE_CONNECTION', 'warehouse'),
];
