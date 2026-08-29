"""Verify that all modules can be imported."""


def test_generic_agent_import():
    import osa.generic_agent

    assert osa.generic_agent.__doc__ is not None


def test_adk_runtime_import():
    import osa.runtimes.adk

    assert osa.runtimes.adk.__doc__ is not None


def test_control_plane_backend_import():
    import osa.control_plane.backend

    assert osa.control_plane.backend.__doc__ is not None
