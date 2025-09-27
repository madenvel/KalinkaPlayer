from sdk.datamodel import EntityId, EntityType


def test_entity_id_creation():
    entity_id = EntityId(id="123", type=EntityType.TRACK, source="test_source")
    assert entity_id.id == "123"
    assert entity_id.type == EntityType.TRACK
    assert entity_id.source == "test_source"


def test_entity_id_to_string():
    entity_id = EntityId(id="123", type=EntityType.TRACK, source="test_source")
    assert str(entity_id.model_dump()) == "kalinka:test_source:track:123"


def test_entity_id_equality():
    entity_id1 = EntityId(id="123", type=EntityType.TRACK, source="test_source")
    entity_id2 = EntityId(id="123", type=EntityType.TRACK, source="test_source")
    assert entity_id1 == entity_id2


def test_entity_id_hash():
    entity_id1 = EntityId(id="123", type=EntityType.TRACK, source="test_source")
    entity_id2 = EntityId(id="123", type=EntityType.TRACK, source="test_source")
    assert hash(entity_id1) == hash(entity_id2)
