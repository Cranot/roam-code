<?php
Schema::create('orders', function ($table) {
    $table->id();
    $table->index('account_id');
});
