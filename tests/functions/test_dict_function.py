def test_pop_item() -> None:
    content = {
        "name": "cloud",
        "age": 34
    }
    content.pop("name")
    assert content == {"age": 34}
    content.pop('name', None)
    content.pop("gender", None)
    assert content == {"age": 34}


if __name__ == "__main__":
    test_pop_item()
