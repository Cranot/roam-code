<?php
// TN: the nearest miss supplies the matching account_id migration index.
class Order extends Model
{
    protected $table = 'orders';

    public function recent()
    {
        return Order::query()->where('account_id', 42)->paginate();
    }
}
