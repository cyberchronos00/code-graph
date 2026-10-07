<?php

use Illuminate\Support\Facades\Broadcast;

Broadcast::channel('store.{storeId}.orders', function ($user, $storeId) {
    return $user->worksAt((int) $storeId);
});
