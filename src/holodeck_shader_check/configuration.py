"""Declarative attribution and opt-in, versioned scene regression profiles."""
PROFILES = {
    "shared-runtime-v1": (),
    "holodeck-grid-v1": ("holodeck-grid",),
    "matrix-corridor-v1": ("matrix-corridor",),
}


def configuration(library):
    file = library.path("authoring.json")
    if file.is_symlink():
        raise ValueError("authoring.json must be a regular file")
    value = library.read_json(file)
    fields = {"schemaVersion", "sourceRequirements", "upstreamRequirements", "regressionProfiles"}
    if not isinstance(value, dict) or set(value) != fields or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("Invalid authoring schema; expected version 1")
    sources, upstreams, profiles = (value[key] for key in ("sourceRequirements", "upstreamRequirements", "regressionProfiles"))
    if not isinstance(sources, dict) or not isinstance(upstreams, dict) or not library.string_list(profiles):
        raise ValueError("Invalid authoring inventories or regression profiles")
    for source, required in sources.items():
        library.path(source)
        if not library.string_list(required):
            raise ValueError(f"Invalid source requirements: {source}")
    for upstream, requirement in upstreams.items():
        if not library.valid_id(upstream) or not isinstance(requirement, dict):
            raise ValueError(f"Invalid upstream requirement: {upstream}")
        license = requirement.get("license")
        if license == "MIT" and set(requirement) == {"license", "noticePath"}:
            library.path(requirement["noticePath"])
        elif license in ("CC BY 3.0", "CC BY-NC-SA 3.0") and set(requirement) == {"license", "uri"}:
            expected = "https://creativecommons.org/licenses/" + ("by/3.0/" if license == "CC BY 3.0" else "by-nc-sa/3.0/")
            if requirement["uri"] != expected:
                raise ValueError(f"Invalid canonical CC URI: {upstream}")
        else:
            raise ValueError(f"Invalid notice requirement: {upstream}")
    for profile in profiles:
        if profile not in PROFILES:
            raise ValueError(f"Unknown regression profile: {profile}")
        for shader in PROFILES[profile]:
            library.find("shader", shader)
    return value
