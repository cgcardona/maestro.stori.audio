"""Resolve merge conflicts in ui.py by merging both sides."""
filepath = "/worktrees/issue-454/maestro/api/routes/musehub/ui.py"

with open(filepath) as f:
    content = f.read()

# Conflict 1: repo_page context — keep both jsonld_script AND og_meta
c1_old = (
    '<<<<<<< HEAD\n'
    '            "jsonld_script": render_jsonld_script(jsonld_repo(repo, page_url)),\n'
    '=======\n'
    '            "og_meta": _og_tags(\n'
    '                title=f"{owner}/{repo_slug} \u2014 Muse Hub",\n'
    '                description=repo.description or f"Music composition repository by {owner}",\n'
    '                og_type="website",\n'
    '            ),\n'
    '>>>>>>> origin/dev'
)
c1_new = (
    '            "jsonld_script": render_jsonld_script(jsonld_repo(repo, page_url)),\n'
    '            "og_meta": _og_tags(\n'
    '                title=f"{owner}/{repo_slug} \u2014 Muse Hub",\n'
    '                description=repo.description or f"Music composition repository by {owner}",\n'
    '                og_type="website",\n'
    '            ),'
)

assert c1_old in content, f"Conflict 1 not found!\nExpected:\n{repr(c1_old)}"
content = content.replace(c1_old, c1_new)
print("Resolved conflict 1")

# Conflict 2: release_detail_page — combine full fetch (HEAD) with json_or_html pattern (dev)
c2_old = (
    '<<<<<<< HEAD\n'
    '    repo, base_url = await _resolve_repo_full(owner, repo_slug, db)\n'
    '    repo_id = str(repo.repo_id)\n'
    '    release = await musehub_releases.get_release_by_tag(db, repo_id, tag)\n'
    '    page_url = str(request.url)\n'
    '    jsonld_script: str | None = None\n'
    '    if release is not None:\n'
    '        jsonld_script = render_jsonld_script(jsonld_release(release, repo, page_url))\n'
    '    return templates.TemplateResponse(\n'
    '        request,\n'
    '        "musehub/pages/release_detail.html",\n'
    '        {\n'
    '            "owner": owner,\n'
    '            "repo_slug": repo_slug,\n'
    '            "repo_id": repo_id,\n'
    '            "tag": tag,\n'
    '            "base_url": base_url,\n'
    '            "current_page": "releases",\n'
    '            "jsonld_script": jsonld_script,\n'
    '        },\n'
    '=======\n'
    '    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)\n'
    '    ctx: dict[str, object] = {\n'
    '        "owner": owner,\n'
    '        "repo_slug": repo_slug,\n'
    '        "repo_id": repo_id,\n'
    '        "tag": tag,\n'
    '        "base_url": base_url,\n'
    '        "current_page": "releases",\n'
    '    }\n'
    '    return json_or_html(\n'
    '        request,\n'
    '        lambda: templates.TemplateResponse(request, "musehub/pages/release_detail.html", ctx),\n'
    '        ctx,\n'
    '>>>>>>> origin/dev\n'
    '    )'
)
c2_new = (
    '    repo, base_url = await _resolve_repo_full(owner, repo_slug, db)\n'
    '    repo_id = str(repo.repo_id)\n'
    '    release = await musehub_releases.get_release_by_tag(db, repo_id, tag)\n'
    '    page_url = str(request.url)\n'
    '    jsonld_script: str | None = None\n'
    '    if release is not None:\n'
    '        jsonld_script = render_jsonld_script(jsonld_release(release, repo, page_url))\n'
    '    ctx: dict[str, object] = {\n'
    '        "owner": owner,\n'
    '        "repo_slug": repo_slug,\n'
    '        "repo_id": repo_id,\n'
    '        "tag": tag,\n'
    '        "base_url": base_url,\n'
    '        "current_page": "releases",\n'
    '        "jsonld_script": jsonld_script,\n'
    '    }\n'
    '    return json_or_html(\n'
    '        request,\n'
    '        lambda: templates.TemplateResponse(request, "musehub/pages/release_detail.html", ctx),\n'
    '        ctx,\n'
    '    )'
)

assert c2_old in content, f"Conflict 2 not found!\nExpected:\n{repr(c2_old)}"
content = content.replace(c2_old, c2_new)
print("Resolved conflict 2")

with open(filepath, "w") as f:
    f.write(content)

# Verify no more conflict markers
assert "<<<<<<< HEAD" not in content, "Still has conflict markers!"
print("All conflicts resolved. File written.")
