"""tests for framework and build tool detection in understand command."""

import json
import subprocess
import sys

from roam.commands.cmd_understand import _matches_import_pattern


def roam(*args, cwd=None):
    """run a roam CLI command and return (output, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "roam"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return result.stdout + result.stderr, result.returncode


class TestImportPatternMatching:
    """test the _matches_import_pattern helper function."""

    def test_exact_match(self):
        """pattern should match exact target."""
        targets = {"react", "vue", "next"}
        assert _matches_import_pattern("react", targets)
        assert _matches_import_pattern("vue", targets)
        assert _matches_import_pattern("next", targets)

    def test_prefix_with_slash(self):
        """pattern should match if followed by slash (js-style paths)."""
        targets = {"next/router", "next/link", "react/jsx-runtime"}
        assert _matches_import_pattern("next", targets)
        assert _matches_import_pattern("react", targets)

    def test_prefix_with_dot(self):
        """pattern should match if followed by dot (namespace-style)."""
        targets = {"microsoft.aspnetcore.mvc", "system.linq"}
        assert _matches_import_pattern("microsoft.aspnetcore", targets)
        assert _matches_import_pattern("system", targets)

    def test_prefix_with_dash(self):
        """pattern should match if followed by dash (package-style)."""
        targets = {"react-dom", "vue-router"}
        assert _matches_import_pattern("react", targets)
        assert _matches_import_pattern("vue", targets)

    def test_prefix_with_at(self):
        """pattern should match if followed by @ (scoped packages)."""
        targets = {"@angular/core", "@vue/runtime"}
        assert _matches_import_pattern("@angular", targets)
        assert _matches_import_pattern("@vue", targets)

    def test_no_substring_match(self):
        """pattern should NOT match arbitrary substring."""
        targets = {"getnextpage", "nextitem", "somereactiveext"}
        assert not _matches_import_pattern("next", targets)
        assert not _matches_import_pattern("react", targets)

    def test_no_partial_word_match(self):
        """pattern should NOT match as part of a word."""
        targets = {"unreacted", "context", "preact"}
        assert not _matches_import_pattern("react", targets)
        assert not _matches_import_pattern("next", targets)

    def test_case_insensitive(self):
        """matching should be case-insensitive."""
        targets = {"microsoft.aspnetcore.mvc", "system.linq"}
        assert _matches_import_pattern("microsoft.aspnetcore", targets)
        assert _matches_import_pattern("MICROSOFT.ASPNETCORE", targets.union({"MICROSOFT.ASPNETCORE.MVC"}))


class TestCSharpFrameworkDetection:
    """test that c# projects are correctly detected as .NET, not next.js."""

    def test_csharp_detected_as_dotnet(self, project_factory):
        """c# project with asp.net should be detected as asp.net and dotnet build."""
        proj = project_factory({
            "Program.cs": """
using Microsoft.AspNetCore.Mvc;
using System.Linq;

namespace MyApp
{
    public class Program
    {
        public static void Main(string[] args)
        {
            var app = WebApplication.Create(args);
            app.Run();
        }
    }

    public class Controller
    {
        public string GetNextPage()
        {
            return "next";
        }
    }
}
""",
            "MyApp.csproj": """
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        frameworks = data.get("tech_stack", {}).get("frameworks", [])
        build_tool = data.get("tech_stack", {}).get("build")

        # should detect asp.net, NOT next.js
        assert "asp.net" in frameworks, f"asp.net not detected in {frameworks}"
        assert "next.js" not in frameworks, f"next.js incorrectly detected in {frameworks}"
        assert build_tool == "dotnet", f"build tool should be dotnet, got {build_tool}"

    def test_csharp_with_entity_framework(self, project_factory):
        """c# project with entity framework should detect both asp.net and entity-framework."""
        proj = project_factory({
            "Data.cs": """
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace MyApp
{
    public class MyDbContext : DbContext
    {
        public DbSet<User> Users { get; set; }
    }

    public class User
    {
        public int Id { get; set; }
        public string NextOfKin { get; set; }
    }
}
""",
            "MyApp.csproj": """
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        frameworks = data.get("tech_stack", {}).get("frameworks", [])

        assert "entity-framework" in frameworks, f"entity-framework not detected in {frameworks}"
        assert "asp.net" in frameworks, f"asp.net not detected in {frameworks}"
        assert "next.js" not in frameworks, f"next.js incorrectly detected in {frameworks}"

    def test_nextjs_still_works(self, project_factory):
        """next.js projects should still be correctly detected."""
        proj = project_factory({
            "pages/index.js": """
import React from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';

export default function Home() {
    const router = useRouter();
    return <div><Link href="/about">About</Link></div>;
}
""",
            "next.config.js": "module.exports = {};",
            "package.json": """
{
  "dependencies": {
    "next": "^14.0.0",
    "react": "^18.0.0"
  }
}
""",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        frameworks = data.get("tech_stack", {}).get("frameworks", [])

        assert "next.js" in frameworks, f"next.js not detected in {frameworks}"
        assert "react" in frameworks, f"react not detected in {frameworks}"

    def test_react_without_nextjs(self, project_factory):
        """react project without next.js should detect react but not next.js."""
        proj = project_factory({
            "App.js": """
import React from 'react';
import ReactDOM from 'react-dom';

function App() {
    return <div>Hello</div>;
}

export default App;
""",
            "package.json": """
{
  "dependencies": {
    "react": "^18.0.0",
    "react-dom": "^18.0.0"
  }
}
""",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        frameworks = data.get("tech_stack", {}).get("frameworks", [])

        assert "react" in frameworks, f"react not detected in {frameworks}"
        assert "next.js" not in frameworks, f"next.js incorrectly detected in {frameworks}"


class TestDotNetBuildDetection:
    """test dotnet build tool detection."""

    def test_csproj_detected(self, project_factory):
        """projects with .csproj should detect dotnet build."""
        proj = project_factory({
            "Program.cs": "class Program { static void Main() {} }",
            "MyApp.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"/>",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        build_tool = data.get("tech_stack", {}).get("build")
        assert build_tool == "dotnet", f"build tool should be dotnet, got {build_tool}"

    def test_sln_detected(self, project_factory):
        """projects with .sln should detect dotnet build."""
        proj = project_factory({
            "Program.cs": "class Program { static void Main() {} }",
            "MySolution.sln": "Microsoft Visual Studio Solution File",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        build_tool = data.get("tech_stack", {}).get("build")
        assert build_tool == "dotnet", f"build tool should be dotnet, got {build_tool}"

    def test_fsproj_detected(self, project_factory):
        """f# projects with .fsproj should detect dotnet build."""
        proj = project_factory({
            "Program.fs": "printfn \"Hello\"",
            "MyApp.fsproj": "<Project Sdk=\"Microsoft.NET.Sdk\"/>",
        })

        out, rc = roam("--json", "understand", cwd=proj)
        assert rc == 0, f"understand failed: {out}"

        data = json.loads(out)
        build_tool = data.get("tech_stack", {}).get("build")
        assert build_tool == "dotnet", f"build tool should be dotnet, got {build_tool}"
