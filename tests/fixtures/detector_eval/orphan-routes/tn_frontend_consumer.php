<?php
// TN: the route segment appears in a frontend consumer, the nearest miss.
Route::get('/api/invoices', [InvoiceController::class, 'index']);
