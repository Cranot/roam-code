<?php
// TP: no frontend or other non-route file consumes this backend endpoint.
Route::get('/api/invoices', [InvoiceController::class, 'index']);
