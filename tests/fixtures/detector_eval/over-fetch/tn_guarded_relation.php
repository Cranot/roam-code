<?php
// TN: the relation has an explicit column list, the detector's documented guard.
return User::query()->with('orders:id,user_id,total')->paginate();
