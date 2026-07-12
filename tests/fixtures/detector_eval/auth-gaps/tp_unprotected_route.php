<?php
// TP: an application route has no auth middleware or inline authorization guard.
Route::get('/admin/reports', [ReportController::class, 'index']);
