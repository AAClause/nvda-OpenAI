class Model:

	def __init__(
		self,
		name: str,
		description: str,
		maxInputToken: int,
		maxOutputToken: int,
		maxTemperature: float=2.0,
		defaultTemperature: float=1.0
	):
		self.name = name
		self.description = description
		self.maxInputToken = maxInputToken
		self.maxOutputToken = maxOutputToken
		self.maxTemperature = maxTemperature
		self.defaultTemperature = defaultTemperature

	def __repr__(self):
		return f"Model(name={self.name}, description={self.description}, maxInputToken={self.maxInputToken}, maxOutputToken={self.maxOutputToken}, maxTemperature={self.maxTemperature}, defaultTemperature={self.defaultTemperature})"

	def __str__(self):
		name = self.name
		description = self.description.rstrip('.')
		maxInputToken = self.maxInputToken
		maxOutputToken = self.maxOutputToken
		return f"{name} ({description}. " + _("Maximum of {maxInputToken} input tokens. Maximum of {maxOutputToken} output tokens.)").format(
			maxInputToken=maxInputToken,
			maxOutputToken=maxOutputToken
		)

	def __hash__(self):
		return hash((self.name, self.maxInputToken, self.maxOutputToken, self.maxTemperature, self.defaultTemperature))
