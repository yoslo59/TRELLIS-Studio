// Keep browser validation in the application's language, regardless of OS locale.
document.addEventListener('invalid',event=>{
 const input=event.target;if(!('validity' in input))return;
 const state=input.validity;let message='Please check this value.';
 if(state.valueMissing)message=input.type==='file'?'Choose a file.':'Please fill in this field.';
 else if(state.rangeUnderflow)message=`Enter a value of at least ${input.min}.`;
 else if(state.rangeOverflow)message=`Enter a value no greater than ${input.max}.`;
 else if(state.stepMismatch)message=`Choose a value using increments of ${input.step}.`;
 else if(state.badInput)message='Enter a valid number.';
 else if(state.tooLong)message=`Use no more than ${input.maxLength} characters.`;
 input.setCustomValidity(message);
},true);
document.addEventListener('input',event=>event.target.setCustomValidity?.(''),true);
