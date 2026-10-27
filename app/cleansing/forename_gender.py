"""Gender commonly associated with UK forenames.

Used only to infer gender when a record has neither a gender field nor a
gendered title, so the gender veto can stop pairs like John / Jenny or
Patrick / Patricia at the same address. Unisex names (Alex, Sam, Jo, Chris,
Charlie, Jamie, Robin, Jordan...) are deliberately absent: an unknown gender
never vetoes anything.

This is a heuristic, not a statement about anyone's gender. Explicit gender
or title on a record always takes precedence.
"""

from __future__ import annotations

_MALE = """
aaron abdul abdullah adam adrian ahmed aidan aiden alan albert alexander alfie alfred ali alistair allan
amir andrew angus anthony antony archibald archie arjun arnold arthur austin barry ben benjamin bernard bill
billy bilal bob bobby brandon brendan brian bruce callum calum cameron carl ciaran clive colin conor connor
craig cyril dale damian damien daniel danny darren david dean declan dennis derek desmond dominic donald
douglas duncan dylan eddie edward edwin elliot elliott eric ethan euan evan ewan fergus finlay finley francis
frank fraser freddie frederick gareth gary gavin geoffrey george gerald gerard gilbert gordon graham grant
gregory guy hamish harold harrison harry harvey hassan hector henry herbert howard hugh hugo ian imran isaac
ivan jack jacob jake james jason jeffrey jeremy jim jimmy joe joel john johnny jon jonathan joseph joshua
jude julian justin karl keith kenneth kevin kieran kwame kyle lawrence lee leo leon leonard lewis liam louis
luke malcolm marcus mark martin matthew max michael mike mohammed mohammad muhammad nathan neil nicholas nigel
noah norman oliver omar oscar owen patrick paul peter philip phillip piotr raymond reece reginald rhys richard
rick ricky robert rodney roger ronald ross rory roy russell ryan samuel scott sean seamus sebastian shane shaun
sidney simon stanley stephen steve steven stewart stuart terence terry theo theodore thomas timothy toby tom
tomasz tommy tony trevor tyler usman victor vincent walter warren wayne wesley william zachary
""".split()

_FEMALE = """
abigail ada adaeze agnes agnieszka aisha alice alison amanda amber amelia amy andrea angela anjali ann anna
anne annie aoife april ava ayesha barbara beatrice becky bella bethan beth betty beverley brenda bridget
caitlin carla carol caroline carolyn carys catherine catriona charlotte chelsea chloe christina christine
ciara claire clare deborah debbie denise diana diane donna doris dorothy eileen eilidh elaine eleanor elizabeth
ella ellie eloise emily emma erin eva evelyn evie faith fatima ffion fiona florence frances freya gemma
georgia gillian gloria grace gwen hannah harriet hazel heather helen holly imogen irene isabel isabella
isabelle isla jacqueline jade jane janet janice jasmine jean jen jennifer jenny jessica jill joan joanna
joanne josephine joyce judith judy julia julie june karen kate katherine kathleen kathryn katie kavita kayleigh
kelly kerry kimberley kirsty laura lauren leanne lily linda lindsay lisa lorraine louise lowri lucy lydia
madison magdalena maisie margaret maria marie marion martha mary maryam maureen megan melanie melissa mia
michelle millie molly morag nancy natalie natasha ngozi niamh nicola olivia paige pamela patricia pauline
penelope phoebe polly poppy priya rachel rebecca rose rosemary ruby ruth samantha sandra sara sarah scarlett
sharon sheila shirley sinead siobhan sophia sophie stacey stephanie sunita susan suzanne tamara tanya teresa
tracey tracy valerie vanessa victoria violet wendy yvonne zainab zara zoe
""".split()

FORENAME_GENDER: dict[str, str] = {**{n: "M" for n in _MALE}, **{n: "F" for n in _FEMALE}}
assert not set(_MALE) & set(_FEMALE), "a name cannot be in both lists"


def gender_from_forename(first_key: str) -> str | None:
    """'M', 'F' or None for an unknown or unisex forename (letters-only, lower case)."""
    return FORENAME_GENDER.get(first_key)
