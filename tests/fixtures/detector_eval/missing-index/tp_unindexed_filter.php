<?php
// TP: a paginated filter targets account_id, a column with no migration index.
class Order extends Model
{
    protected $table = 'orders';

    public function recent()
    {
        return Order::query()->where('account_id', 42)->paginate();
    }
}
