<?php
namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class ReportController extends Controller
{
    // Read method, no authorization call (auth-gaps: low).
    public function index(Request $request)
    {
        return Report::all();
    }

    // Mutating CRUD method, no $this->authorize() (auth-gaps: high).
    public function store(Request $request)
    {
        $name = $request->input('name');
        return DB::table('reports')->whereRaw("name = '$name'")->get();
    }

    // Mutating CRUD method, no $this->authorize() (auth-gaps: high).
    public function destroy($id)
    {
        return Report::destroy($id);
    }
}
