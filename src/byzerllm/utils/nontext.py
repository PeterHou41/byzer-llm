from typing import List, Dict, Any, Union, Optional
import pydantic


class Tag(pydantic.BaseModel):
    start_tag: str
    end_tag: str
    content: Union[str, List["Tag"], "Tag"]
    parent: Optional["Tag"] = None


class TagExtractor:
    def __init__(self, text: str):
        self.text = text
        self.pos = -1
        self.len = len(text)
        self.root_tag = Tag(start_tag="<_ROOT_>", end_tag="</_ROOT_>", content=[])
        self.current_tag = None

    def peek(self) -> str:
        if self.pos + 1 < self.len:
            return self.text[self.pos + 1]
        return ""

    def peek2(self) -> str:
        if self.pos + 1 < self.len:
            return self.text[self.pos + 2]
        return ""

    def peek3(self) -> str:
        if self.pos + 1 < self.len:
            return self.text[self.pos + 3]
        return ""

    def next(self) -> str:
        if self.pos < self.len - 1:
            self.pos += 1
            char = self.text[self.pos]
            return char
        return ""

    def is_full_tag(self) -> bool:
        return self.current_tag.start_tag and self.current_tag.end_tag

    def is_start_tag(self) -> bool:
        return self.peek() and self.peek() == "<" and self.peek2() == "_"

    def extract_start_tag(self) -> str:
        tag = ""

        while self.peek() and self.peek() != ">":
            tag += self.next()
        tag += self.next()

        if self.current_tag is None or self.current_tag == self.root_tag:
            self.current_tag = Tag(
                start_tag=tag, end_tag="", content="", parent=self.root_tag
            )
            self.root_tag.content.append(self.current_tag)
        ## 当前tag已经闭合，找到当前tag的父tag,遇到新tag
        elif self.is_full_tag():
            parent = self.current_tag.parent or self.current_tag
            current_tag = Tag(
                start_tag=tag,
                end_tag="",
                content="",
                parent=parent,
            )
            if isinstance(parent.content, list):
                parent.content.append(current_tag)
            else:
                s = self.current_tag.content
                parent.content = []
                if s:
                    parent.content.append(s)
                parent.content.append(current_tag)
            self.current_tag = current_tag
        ## 当前tag还没有闭合，直接添加到当前tag的content中
        elif not self.is_full_tag():
            current_tag = Tag(
                start_tag=tag, end_tag="", content="", parent=self.current_tag
            )
            if isinstance(self.current_tag.content, list):
                self.current_tag.content.append(current_tag)
            else:
                s = self.current_tag.content
                self.current_tag.content = []
                if s:
                    self.current_tag.content.append(s)
                self.current_tag.content.append(current_tag)
            self.current_tag = current_tag

        return tag

    def is_end_tag(self) -> bool:
        return (
            self.peek()
            and self.peek() == "<"
            and self.peek2() == "/"
            and self.peek3() == "_"
        )

    def extract_end_tag(self) -> str:
        tag = ""
        while self.peek() and self.peek() != ">":
            tag += self.next()
        tag += self.next()
        self.current_tag.end_tag = tag
        self.current_tag = self.current_tag.parent
        return tag

    def consume_blank(self):
        while (
            self.peek() == "\n"
            or self.peek() == " "
            or self.peek() == "\t"
            or self.peek() == "\r"
        ):
            self.next()

    def is_in_tag(self) -> bool:
        return (
            self.current_tag
            and self.current_tag.start_tag
            and not self.current_tag.end_tag
        )

    def is_tag_content(self) -> bool:
        if not self.root_tag:
            return False
        temp_pos = self.pos
        self.consume_blank()
        if self.is_start_tag():
            self.pos = temp_pos
            return True
        if self.is_end_tag():
            self.pos = temp_pos
            return True
        return False

    def extract_str_content(self) -> str:
        content = ""
        while not self.is_start_tag() and not self.is_end_tag():
            content += self.next()

        self.current_tag.content = content
        return content

    def is_not_in_tag_str(self) -> bool:
        if not self.root_tag:
            return True
        if not self.current_tag:
            return True
        if not self.current_tag.start_tag and not self.current_tag.end_tag:
            return True
        if self.current_tag.start_tag and self.current_tag.end_tag:
            return True
        return False

    def extract_content_not_in_tag(self) -> str:
        content = ""
        while self.peek() and not self.is_start_tag() and not self.is_end_tag():
            content += self.next()
        return content

    def extract(self) -> Union[Tag]:
        if self.is_extracted:
            return self.root_tag
        while True:
            if self.pos == self.len - 1:
                break
            if self.is_start_tag():
                self.extract_start_tag()
            elif self.is_end_tag():
                self.extract_end_tag()
            elif self.is_in_tag():
                if self.is_tag_content():
                    self.consume_blank()
                    continue
                else:
                    self.extract_str_content()
            elif self.is_not_in_tag_str():
                self.extract_content_not_in_tag()
        self.is_extracted = True
        return self.root_tag


class Image(TagExtractor):

    def __init__(self, text: str):
        super().__init__(text)
        self.is_extracted = False

    def has_image(self) -> bool:
        self.extract()
        for item in self.root_tag.content:
            if isinstance(item, Tag) and item.start_tag == "<_image_>":
                return True
        return False

    @staticmethod
    def load_image_form_path(path: str) -> str:
        '''
        Load image from path and return base64 image data
        '''
        import base64
        import os

        file_extension = os.path.splitext(path)[1][1:].lower()
        image_type = file_extension

        with open(path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"<_image_>data:image/{image_type};base64,{encoded_string}</_image_>"

    @staticmethod
    def save_image_to_path(
        image_data: str, output_path: str, auto_fix_suffix: bool = False
    ) -> str:
        '''
        Save base64 image data as image file
        '''
        import base64
        import os

        # Extract image type and base64 data
        data_prefix = "data:image/"
        base64_prefix = ";base64,"
        if not image_data.startswith(data_prefix):
            raise ValueError("Invalid image data format")

        format_end = image_data.index(base64_prefix)
        image_type = image_data[len(data_prefix) : format_end]
        base64_data = image_data[format_end + len(base64_prefix) :]

        # Decode the base64 image data
        image_data = base64.b64decode(base64_data)

        # Check and fix file extension if necessary
        file_name, file_extension = os.path.splitext(output_path)
        correct_extension = f".{image_type}"

        if auto_fix_suffix and file_extension.lower() != correct_extension.lower():
            output_path = file_name + correct_extension

        # Write the image data to the file
        with open(output_path, "wb") as image_file:
            image_file.write(image_data)

        return output_path

    def to_content(self) -> List[Dict[str, str]]:
        self.extract()

        result = []
        current_item = {}

        for item in self.root_tag.content:
            if isinstance(item, Tag) and item.start_tag == "<_image_>":
                if current_item:
                    result.append(current_item)
                    current_item = {}
                current_item["image"] = item.content

        if current_item:
            result.append(current_item)

        new_text = self.text
        for res in result:
            new_text = new_text.replace(f"<_image_>{res['image']}</_image_>", "")

        result.append({"text": new_text.strip()}) 
        
        # for item in result:
        #     if "image" in item:
        #         print(item["image"][0:100] + "...")
        #     else:
        #         print(item["text"][0:100] + "...")
        return result
