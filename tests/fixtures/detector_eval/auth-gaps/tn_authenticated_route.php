<?php
// TN: the route is inside the detector's documented auth middleware group.
Route::middleware(['auth:sanctum'])->group(function () {
    Route::get('/admin/reports', [ReportController::class, 'index']);
});
