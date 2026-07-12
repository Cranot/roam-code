<?php
// TP: eager-loading a relation without a column list exposes the full relation.
return User::query()->with('orders')->paginate();
