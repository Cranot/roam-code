<?php
use Illuminate\Support\Facades\Route;

// Unprotected admin routes — no auth middleware anywhere (auth-gaps: high).
Route::get('/admin/reports', [ReportController::class, 'index']);
Route::post('/admin/reports', [ReportController::class, 'store']);
Route::delete('/admin/reports/{id}', [ReportController::class, 'destroy']);

// Protected group — auth-gaps must NOT flag this route.
Route::middleware(['auth:sanctum'])->group(function () {
    Route::get('/admin/audit', [AuditController::class, 'index']);
});
